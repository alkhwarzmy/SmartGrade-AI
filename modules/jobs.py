"""Durable OCR/report tasks. No tokens, passwords or paid provider requests enter the queue."""

from pathlib import Path
from sqlalchemy import select, func
from database.models import Job, now
from modules.workspace import tenant_write_lock
from modules.auth import PermissionDenied, CapacityError
from modules.review import RevisionConflict


class Jobs:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = workspace._core

    def create(self, kind, payload):
        actor = self.workspace._actor("write" if kind == "ocr" else None, results=kind == "report")
        if kind == "ocr":
            row = self.workspace.submission(payload["sid"])
            if row.revision != payload["revision"]:
                raise RevisionConflict("تغيرت الإجابة؛ أعد تحميلها")
            if self.workspace.exam(row.exam_id).kind != "essay":
                raise ValueError("OCR مخصص للمقالات")
        elif kind == "report":
            self.workspace.exam(payload["exam_id"])
            if payload["format"] not in {"csv", "xlsx", "pdf"}:
                raise ValueError("صيغة غير مدعومة")
        else:
            raise ValueError("نوع مهمة غير مدعوم")
        with tenant_write_lock(self.store.root), self.store.sessions.begin() as db:
            self.workspace._guard_storage()
            active = db.scalar(
                select(func.count()).select_from(Job).where(Job.state.in_(["queued", "running"]))
            )
            if active >= 20:
                raise CapacityError("طابور المؤسسة ممتلئ؛ انتظر اكتمال بعض المهام")
            job = Job(user_id=actor.user_id, kind=kind, payload=payload)
            db.add(job)
            db.flush()
            self.store._event(db, self.workspace._audit(actor, "job.queued"), job.id)
            result = job.id
        self.workspace._deliver()
        return result

    def get(self, job_id):
        actor = self.workspace._actor(results=True)
        with self.store.sessions() as db:
            row = db.get(Job, job_id)
            if not row or (row.user_id != actor.user_id and not actor.can("users")):
                raise PermissionDenied("المهمة غير متاحة")
            # Lost read permission must also revoke access to an OCR result/task.
            if row.kind == "ocr" and not actor.can("read"):
                raise PermissionDenied("المهمة غير متاحة")
            return row

    def change(self, job_id, action):
        self.get(job_id)
        with tenant_write_lock(self.store.root), self.store.sessions.begin() as db:
            actor = self.workspace._actor(results=True)
            row = db.get(Job, job_id)
            if action == "cancel" and row.state in {"queued", "running"}:
                row.state = "cancelled"
            elif action == "retry" and row.state in {"failed", "cancelled"}:
                if (
                    db.scalar(
                        select(func.count()).select_from(Job).where(Job.state.in_(["queued", "running"]))
                    )
                    >= 20
                ):
                    raise CapacityError("طابور المؤسسة ممتلئ")
                row.state, row.progress, row.error = "queued", 0, ""
            else:
                raise RevisionConflict("حالة المهمة لا تسمح بهذا الإجراء")
            row.updated_at = now()
            self.store._event(db, self.workspace._audit(actor, "job." + action), job_id)
        self.workspace._deliver()

    def result_path(self, job_id):
        row = self.get(job_id)
        if row.state != "succeeded" or row.kind != "report":
            raise ValueError("ملف التقرير غير جاهز")
        from datetime import datetime, timezone

        if (datetime.now(timezone.utc) - datetime.fromisoformat(row.updated_at)).total_seconds() > 3600:
            raise ValueError("انتهت صلاحية التقرير؛ أنشئ تقريرًا جديدًا")
        # Do not trust paths in a JSON blob; reconstruct from server-generated IDs and an enum.
        if row.payload["format"] not in {"csv", "xlsx", "pdf"}:
            raise ValueError("صيغة غير صالحة")
        return Path(self.store.root) / "job-results" / f"{row.id}.{row.payload['format']}"
