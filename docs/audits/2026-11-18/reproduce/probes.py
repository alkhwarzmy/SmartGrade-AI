"""Reproduce assessment observations using isolated synthetic state only."""

from pathlib import Path
import os
import sys
import tempfile

ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "api.py").is_file() and (p / "pyproject.toml").is_file()
)
sys.path.insert(0, str(ROOT))


def run():
    import json
    import sqlite3
    import tempfile
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from api import create_app
    from modules.auth import AuthService, LoginRateLimited
    from modules.review import Store
    from modules.schemas import ExamSpec, Criterion
    from modules.preprocessing import png_bytes
    from modules.mcq_grader import template

    OUT = ROOT / "work" / "audit-2026-11-18"
    OUT.mkdir(parents=True, exist_ok=True)
    PASSWORD = "Audit-SYNTHETIC-only-Pass!2026"
    results = []

    def result(code, observed, acceptance, severity, evidence):
        results.append(
            {
                "id": code,
                "observed": observed,
                "proposed_acceptance": acceptance,
                "recommended_priority": severity,
                "evidence_kind": evidence,
            }
        )

    with tempfile.TemporaryDirectory(prefix="smartgrade-assessment-") as temporary:
        base = Path(temporary)
        # Shared gateway / NAT: a failure bucket must not disable a different institution's valid login.
        auth = AuthService(base / "throttle")
        auth.provision_organization("school-one", "SYNTHETIC One", "admin@one.test", "Admin", PASSWORD)
        auth.provision_organization("school-two", "SYNTHETIC Two", "admin@two.test", "Admin", PASSWORD)
        for i in range(30):
            try:
                auth.login(
                    "unknown-school", f"unknown{i}@invalid.test", "incorrect", client_id="shared-proxy"
                )
            except ValueError:
                pass
        blocked = False
        try:
            auth.login("school-two", "admin@two.test", PASSWORD, client_id="shared-proxy")
        except LoginRateLimited:
            blocked = True
        assert blocked
        result(
            "F01",
            {
                "wrong_attempts": 30,
                "different_organization_valid_login_blocked": blocked,
                "shared_client": "simulated reverse-proxy/NAT",
                "lockout_seconds": 900,
            },
            "Scope account/institution throttles and configure trusted proxy client addresses without a cross-tenant hard lock.",
            "P1",
            "reproduced",
        )

        app = create_app(base / "api")
        app.state.auth.provision_organization(
            "school-one", "SYNTHETIC API", "admin@one.test", "Admin", PASSWORD
        )
        with TestClient(app) as client:
            login = client.post(
                "/auth/login",
                json={"organization": "school-one", "email": "admin@one.test", "password": PASSWORD},
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            # Simulate an identity-audit storage failure after the separate exam DB committed.
            spec = ExamSpec(title="SYNTHETIC fault test", kind="mcq", answer_key=["A"] * 20)
            with patch.object(
                app.state.auth,
                "audit",
                side_effect=sqlite3.OperationalError("synthetic injected audit failure"),
            ):
                failed = client.post("/exams", json=spec.model_dump(), headers=headers)
            rows = client.get("/exams", headers=headers).json()
            assert failed.status_code == 409 and len(rows) == 1
            result(
                "F02",
                {
                    "http_status": failed.status_code,
                    "exams_persisted_despite_error": len(rows),
                    "fault": "injected audit failure after exam commit",
                },
                "Use a transactional outbox/durable event so success and client-visible outcome are unambiguous.",
                "P1",
                "fault_injection",
            )
            eid = rows[0]["id"]
            # A replay with the SAME idempotency key currently creates a separate submission each time.
            payload = png_bytes(template({0: ["A"]}, True))
            replay_headers = {**headers, "Idempotency-Key": "SYNTHETIC-same-request-001"}
            response1 = client.post(
                f"/exams/{eid}/submissions",
                headers=replay_headers,
                data={"student_code": "SYNTHETIC-REPLAY"},
                files={"file": ("sheet.png", payload, "image/png")},
            )
            response2 = client.post(
                f"/exams/{eid}/submissions",
                headers=replay_headers,
                data={"student_code": "SYNTHETIC-REPLAY"},
                files={"file": ("sheet.png", payload, "image/png")},
            )
            assert response1.status_code == response2.status_code == 201
            assert response1.json()["id"] != response2.json()["id"]
            result(
                "F03",
                {
                    "identical_replays": 2,
                    "distinct_submission_ids": 2,
                    "same_idempotency_key": True,
                    "response_statuses": [201, 201],
                },
                "Persist request keys per institution and operation; replay returns the same result, while an intentional new attempt has a new key.",
                "P1",
                "reproduced_missing_capability",
            )
            # All stale revisions should use the same conflict contract and never call the model.
            essay = ExamSpec(
                title="SYNTHETIC Essay",
                kind="essay",
                question="لماذا الماء مهم؟",
                maximum=4,
                criteria=[Criterion(id="c1", description="أهمية الماء", maximum=4)],
            )
            eeid = client.post("/exams", json=essay.model_dump(), headers=headers).json()["id"]
            sid = client.post(
                f"/exams/{eeid}/submissions",
                headers=headers,
                data={"student_code": "SYNTHETIC-ESSAY"},
                files={"file": ("essay.png", payload, "image/png")},
            ).json()["id"]
            assert (
                client.put(
                    f"/submissions/{sid}/text", headers=headers, json={"text": "الماء مهم", "revision": 1}
                ).status_code
                == 200
            )
            app.state.auth.set_ai_policy(login.json()["access_token"], True)
            with patch(
                "api.propose_grade", side_effect=AssertionError("Network/provider must not be called")
            ) as model:
                stale = client.post(
                    f"/submissions/{sid}/proposal",
                    headers=headers,
                    json={"revision": 1, "consent_to_external_processing_and_cost": True},
                )
                assert not model.called
            assert stale.status_code == 400
            result(
                "F06",
                {
                    "stale_proposal_http_status": stale.status_code,
                    "other_revision_routes_use": 409,
                    "provider_called": False,
                },
                "Return 409 consistently for every stale revision.",
                "P2",
                "reproduced",
            )

        store = Store(base / "file-delete")
        eid = store.create_exam(ExamSpec(title="SYNTHETIC Delete", kind="mcq", answer_key=["A"] * 20))
        sid = store.upload(eid, "SYNTHETIC-DELETE", png_bytes(template({}, True)))
        row = store.submission(sid)
        image = store.image_path(row.image_name)
        original_unlink = Path.unlink

        def deny_image_delete(path, *args, **kwargs):
            if path.parent == store.images:
                raise PermissionError("synthetic injected file deletion error")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", deny_image_delete):
            try:
                store.delete_submission(sid, row.revision)
            except PermissionError:
                pass
        db_missing = not store.submissions()
        orphan_exists = image.exists()
        assert db_missing and orphan_exists
        result(
            "F04",
            {
                "submission_row_deleted": db_missing,
                "original_image_still_exists": orphan_exists,
                "fault": "injected unlink failure after DB commit",
            },
            "Mark deletion pending, retry file erasure, then finalize; monitor and reconcile orphaned images.",
            "P1",
            "fault_injection",
        )

        store = Store(base / "noop")
        essay = ExamSpec(
            title="SYNTHETIC No-op",
            kind="essay",
            question="لماذا الماء مهم؟",
            maximum=4,
            criteria=[Criterion(id="c1", description="أهمية الماء", maximum=4)],
        )
        eid = store.create_exam(essay)
        sid = store.upload(eid, "SYNTHETIC-NOOP", png_bytes(template({}, True)))
        store.save_text(sid, "الماء مهم", "synthetic-reviewer", 1)
        grade = {
            "criteria": [
                {
                    "criterion_id": "c1",
                    "score": 3,
                    "evidence": "الماء",
                    "rationale": "إجابة مختصرة",
                    "missing": "مثال",
                }
            ],
            "needs_review": True,
        }
        store.approve_essay(sid, grade, "synthetic-reviewer", "", 2, True)
        before = store.submission(sid)
        store.save_text(sid, before.text, "synthetic-reviewer", before.revision)
        after = store.submission(sid)
        assert before.status == "approved" and after.status == "review" and after.total is None
        result(
            "F05",
            {
                "text_changed": False,
                "before_status": before.status,
                "before_total": before.total,
                "after_status": after.status,
                "after_total": after.total,
            },
            "A no-op save preserves approval; a real text change continues to invalidate it.",
            "P1",
            "reproduced",
        )

    (OUT / "probes.json").write_text(
        json.dumps(
            {
                "source_commit": "40fe03940c42c696577c4866f8769349fdb0dca6",
                "synthetic_data_only": True,
                "live_provider_calls": 0,
                "observations": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="audit-import-context-") as isolated:
        os.environ["SMARTGRADE_DATA_DIR"] = isolated
        os.environ["ALLOW_EXTERNAL_AI"] = "false"
        os.environ["ALLOW_PAID_API"] = "false"
        run()
