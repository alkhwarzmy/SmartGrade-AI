"""One durable worker per installation. Executes each OCR/report in a bounded child process."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from sqlalchemy import select, func
from database.models import Job, Submission, FileAsset, now
from modules.auth import AuthService, PERMISSIONS
from modules.review import RevisionConflict
from modules.workspace import tenant_write_lock, _store
from modules.outbox import flush_outbox
from config import DATA_DIR
from modules.processing import ProcessingError, OCR_CODES, MESSAGES, safe_code


def require_job_actor(auth, organization_id, user_id, kind):
    with auth.connection() as db:
        user = db.execute(
            "SELECT u.*,o.active AS org_active,o.renewal_until,o.name AS org_name FROM users u JOIN organizations o ON u.organization_id=o.id WHERE u.id=? AND o.id=?",
            (user_id, organization_id),
        ).fetchone()
    if (
        not user
        or not user["active"]
        or not user["org_active"]
        or user["must_change_password"]
        or (user["renewal_until"] is not None and user["renewal_until"] <= time.time())
    ):
        raise ValueError("job_permission_revoked")
    if kind == "ocr" and "write" not in PERMISSIONS[user["role"]]:
        raise ValueError("job_permission_revoked")
    return user


def execute_job(root, organization_id, job_id):
    auth = AuthService(root)
    store = _store(str(auth.root / "organizations" / organization_id))
    with store.sessions() as db:
        job = db.get(Job, job_id)
        if not job or job.state != "running":
            return
    user = require_job_actor(auth, organization_id, job.user_id, job.kind)
    audit = {"organization_id": organization_id, "user_id": job.user_id, "action": "job.completed"}
    payload = job.payload
    content = None
    if job.kind == "ocr":
        from modules.handwriting_ocr import recognize

        with tenant_write_lock(store.root):
            row = store.submission(payload["sid"])
            if row.revision != payload["revision"]:
                raise RevisionConflict("stale_revision")
            raw = store.image_path(row.image_name).read_bytes()
        text = recognize(raw)
        with tenant_write_lock(store.root):
            with store.sessions() as db:
                current = db.get(Job, job_id)
                if current.state != "running":
                    return
            require_job_actor(auth, organization_id, job.user_id, job.kind)
            if store.storage_bytes() + 262144 > 1024**2 * auth_plan_storage(auth, organization_id):
                raise ValueError("storage_quota")
            store.save_text(
                row.id,
                text,
                job.user_id,
                payload["revision"],
                source="ocr",
                audit={**audit, "action": "submission.text_changed"},
            )
            with store.sessions.begin() as db:
                current = db.get(Job, job_id)
                current.result = {"revision": store.submission(row.id).revision}
                current.state, current.progress, current.updated_at = "succeeded", 100, now()
                store._event(db, audit, job_id)
        return
    if job.kind != "report":
        raise ValueError("unsupported_job")
    from modules.reports import frames, csv_bytes, excel_bytes

    with tenant_write_lock(store.root):
        with store.sessions() as db:
            count = db.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.exam_id == payload["exam_id"], Submission.status == "approved")
            )
        if count > 2500 or (payload["format"] == "pdf" and count > 2000):
            raise ValueError("report_too_large_split_exam")
        summary, details = frames(store, payload["exam_id"])
        if payload["format"] == "xlsx":
            content = excel_bytes(summary, details)
        elif payload["format"] == "csv":
            content = csv_bytes(summary)
        elif payload["format"] == "pdf":
            from modules.pdf_reports import pdf_bytes

            content = pdf_bytes(summary, user["org_name"])
        else:
            raise ValueError("unsupported_format")
        require_job_actor(auth, organization_id, job.user_id, job.kind)
        folder = store.root / "job-results"
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / f"{job.id}.{payload['format']}"
        with store.sessions.begin() as db:
            current = db.get(Job, job_id)
            if current.state != "running":
                return
            if store.storage_bytes() + len(content) > 1024**2 * auth_plan_storage(auth, organization_id):
                raise ValueError("storage_quota")
            fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as target:
                target.write(content)
            db.merge(FileAsset(name=f"job-results/{path.name}", size=len(content)))
            current.result = {"bytes": len(content)}
            current.state, current.progress, current.updated_at = "succeeded", 100, now()
            store._event(db, audit, job.id)


def auth_plan_storage(auth, oid):
    from modules.auth import PLAN_LIMITS

    with auth.connection() as db:
        plan = db.execute("SELECT plan FROM organizations WHERE id=?", (oid,)).fetchone()[0]
    return PLAN_LIMITS[plan]["storage_mb"]


def sweep(store):
    with store.sessions.begin() as db:
        for job in db.scalars(select(Job).where(Job.state == "succeeded", Job.kind == "report")):
            from datetime import datetime, timezone

            if (
                time.time() - datetime.fromisoformat(job.updated_at).astimezone(timezone.utc).timestamp()
                > 3600
            ):
                job.state = "expired"
        expired = list(
            db.scalars(
                select(Job).where(Job.state.in_(["failed", "cancelled", "expired"]), Job.kind == "report")
            )
        )
    for job in expired:
        try:
            (store.root / "job-results" / f"{job.id}.{job.payload['format']}").unlink(missing_ok=True)
        except OSError:
            continue  # Keep the job reference and retry next cycle.
        with store.sessions.begin() as db:
            asset = db.get(FileAsset, f"job-results/{job.id}.{job.payload['format']}")
            if asset:
                db.delete(asset)


def fail_job(root, organization_id, job_id, code):
    auth = AuthService(root)
    store = _store(str(auth.root / "organizations" / organization_id))
    code = safe_code(code)
    with tenant_write_lock(store.root):
        with store.sessions() as db:
            job = db.get(Job, job_id)
            if not job or job.state != "running":
                return
        if job.kind == "ocr" and code in OCR_CODES:
            try:
                require_job_actor(auth, organization_id, job.user_id, job.kind)
                store.record_processing_failure(job.payload["sid"], "ocr", job.payload["revision"], code=code)
            except RevisionConflict:
                code = "stale_revision"
            except ValueError:
                code = "job_permission_revoked"
        with store.sessions.begin() as db:
            current = db.get(Job, job_id)
            if current and current.state == "running":
                current.state, current.error, current.updated_at = "failed", code, now()


def cycle(root, *, run_children=True):
    auth = AuthService(root)
    with auth.connection() as db:
        organizations = [r[0] for r in db.execute("SELECT id FROM organizations")]
    processed = 0
    for oid in organizations:
        folder = auth.root / "organizations" / oid
        if not (folder / "smartgrade.sqlite3").exists():
            continue
        (auth.root / "worker-heartbeat").write_text(str(int(time.time())))
        store = _store(str(folder))
        with tenant_write_lock(folder):
            store.retry_deletions()
            flush_outbox(store, auth)
            sweep(store)
            marker = folder / ".last-reconcile"
            if not marker.exists() or time.time() - marker.stat().st_mtime > 3600:
                from modules.operations import reconcile_files

                reconcile_files(store, execute=True)
                marker.touch()
            with store.sessions.begin() as db:
                # Caller holds the installation worker lock: surviving running tasks are from a dead worker.
                for interrupted in db.scalars(select(Job).where(Job.state == "running")):
                    interrupted.state, interrupted.error = "failed", "worker_interrupted_retry_required"
                job = db.scalar(
                    select(Job).where(Job.state == "queued").order_by(Job.created_at, Job.id).limit(1)
                )
                if not job:
                    continue
                job.state, job.progress, job.updated_at = "running", 10, now()
                job.attempts += 1
                job_id = job.id
        error = ""
        try:
            if run_children:
                outcome = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.worker",
                        "--data-dir",
                        str(auth.root),
                        "--execute",
                        oid,
                        job_id,
                    ],
                    timeout=120,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if outcome.returncode:
                    error = "processing_failed_review_inputs"
            else:
                execute_job(root, oid, job_id)
        except subprocess.TimeoutExpired:
            error = "time_limit_120s"
        except ProcessingError as exc:
            error = exc.code
        except RevisionConflict:
            error = "stale_revision"
        except ValueError as exc:
            error = str(exc) if str(exc) in MESSAGES else "processing_failed_review_inputs"
        except Exception:
            error = "processing_failed_review_inputs"
        fail_job(root, oid, job_id, error or "processing_interrupted")
        with tenant_write_lock(folder), store.sessions.begin() as db:
            current = db.get(Job, job_id)
            if current and current.state == "running":
                current.state, current.error, current.updated_at = (
                    "failed",
                    error or "processing_interrupted",
                    now(),
                )
        processed += 1
    (auth.root / "worker-heartbeat").write_text(str(int(time.time())))
    return processed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--execute", nargs=2, metavar=("ORGANIZATION_ID", "JOB_ID"))
    args = parser.parse_args()
    if args.execute:
        import re

        if not all(re.fullmatch("[a-f0-9]{32}", v) for v in args.execute):
            raise ValueError("invalid_job_id")
        try:
            execute_job(args.data_dir, *args.execute)
        except ProcessingError as exc:
            fail_job(args.data_dir, *args.execute, exc.code)
            raise SystemExit(1) from None
        except RevisionConflict:
            fail_job(args.data_dir, *args.execute, "stale_revision")
            raise SystemExit(1) from None
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc) in MESSAGES else "processing_failed_review_inputs"
            fail_job(args.data_dir, *args.execute, code)
            raise SystemExit(1) from None
        return
    import fcntl

    auth = AuthService(args.data_dir)
    with (auth.root / ".worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                count = cycle(auth.root)
                if args.once:
                    print(json.dumps({"processed": count}))
                    return
            except Exception:
                if args.once:
                    raise
                # Sanitized liveness: no answers, tokens or provider errors enter logs.
                print("worker_cycle_failed", file=sys.stderr, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
