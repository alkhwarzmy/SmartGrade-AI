"""Privacy-safe storage/queue checks used by readiness, operator monitoring and tenant admins."""

from pathlib import Path
import os
import shutil
import tempfile
from datetime import datetime, timezone
from sqlalchemy import select, func
from database.models import Outbox, PendingDeletion, Job


def storage_ready(root):
    root = Path(root)
    minimum = int(os.getenv("SMARTGRADE_MIN_FREE_MB", "64")) * 1024**2
    if shutil.disk_usage(root).free < minimum:
        return False
    with tempfile.TemporaryFile(dir=root) as handle:
        handle.write(b"ready")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def tenant_status(store):
    with store.sessions() as db:
        pending = db.scalar(select(func.count()).select_from(Outbox))
        oldest = db.scalar(select(func.min(Outbox.created_at)))
        deletions = db.scalar(select(func.count()).select_from(PendingDeletion))
        states = dict(db.execute(select(Job.state, func.count()).group_by(Job.state)).all())
    age = (
        max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(oldest)).total_seconds()))
        if oldest
        else 0
    )
    heartbeat = store.root.parent.parent / "worker-heartbeat"
    worker_age = (
        max(0, int(datetime.now(timezone.utc).timestamp() - heartbeat.stat().st_mtime))
        if heartbeat.exists()
        else None
    )
    return {
        "audit_pending": pending,
        "audit_oldest_seconds": age,
        "deletions_pending": deletions,
        "jobs": states,
        "worker_heartbeat_seconds": worker_age,
        "alerts": [
            label
            for condition, label in [
                (age > 300, "audit_backlog"),
                (
                    bool(states.get("queued")) and (worker_age is None or worker_age > 180),
                    "worker_unavailable",
                ),
                (deletions > 0, "deletions_pending"),
            ]
            if condition
        ],
    }


def reconcile_files(store, *, execute=False, minimum_age=3600):
    """Caller holds tenant write lock; never remove a referenced file, including a pending deletion."""
    import re
    import time
    from database.models import Submission, FileAsset

    with store.sessions() as db:
        names = {
            name
            for pair in db.execute(select(Submission.image_name, Submission.aligned_name))
            for name in pair
            if name
        }
    candidates = deleted = 0
    for path in store.images.glob("*.png"):
        if path.name in names or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{32}\.png", path.name):
            continue
        if time.time() - path.stat().st_mtime < minimum_age:
            continue
        candidates += 1
        if execute:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            with store.sessions.begin() as db:
                row = db.get(FileAsset, "images/" + path.name)
                if row:
                    db.delete(row)
            deleted += 1
    return {"orphan_candidates": candidates, "deleted": deleted, "dry_run": not execute}
