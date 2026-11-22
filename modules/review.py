"""Application service boundary. UI cannot bypass grade/state validation."""

from copy import deepcopy
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from sqlalchemy import select, exists, func
from database.db import make_engine, session_factory
from database.models import (
    Exam,
    Submission,
    ReviewHistory,
    Outbox,
    RequestReceipt,
    PendingDeletion,
    Job,
    FileAsset,
    now,
)
from modules.resources import bounded_processing
from modules.schemas import ExamSpec, validate_answers, parse_essay_grade
from modules.preprocessing import decode_image, png_bytes
from modules.mcq_grader import read_answers, score_answers


class RevisionConflict(ValueError):
    pass


class IdempotencyConflict(RevisionConflict):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.images = self.root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(self.root)
        self.sessions = session_factory(self.engine)

    @staticmethod
    def _event(db, audit, object_id):
        if audit:
            db.add(Outbox(payload={**audit, "object_id": object_id}))

    def record_event(self, audit, object_id):
        with self.sessions.begin() as db:
            self._event(db, audit, object_id)

    def receipt(self, key, fingerprint):
        if not key:
            return None
        with self.sessions() as db:
            row = db.get(RequestReceipt, key)
            if row:
                if row.fingerprint != fingerprint:
                    raise IdempotencyConflict("استُخدم مفتاح الطلب لمحتوى مختلف")
                if not db.get(Submission, row.submission_id) or db.get(PendingDeletion, row.submission_id):
                    raise IdempotencyConflict("الطلب السابق حُذف؛ استخدم مفتاحًا جديدًا لمحاولة جديدة")
                return row.submission_id

    def create_exam(self, spec: ExamSpec, *, audit=None) -> str:
        spec = ExamSpec.model_validate(spec.model_dump())
        with self.sessions.begin() as db:
            row = Exam(title=spec.title, kind=spec.kind, spec=spec.model_dump())
            db.add(row)
            db.flush()
            self._event(db, audit, row.id)
            return row.id

    def exams(self, limit=None, offset=0):
        with self.sessions() as db:
            stmt = select(Exam).order_by(Exam.created_at.desc(), Exam.id)
            if limit is not None:
                stmt = stmt.limit(limit).offset(offset)
            return list(db.scalars(stmt))

    def exam(self, exam_id):
        with self.sessions() as db:
            exam = db.get(Exam, exam_id)
            if not exam:
                raise ValueError("الاختبار غير موجود")
            return exam

    def counts(self, exam_id=None):
        with self.sessions() as db:
            statement = select(Submission.status, func.count(), func.avg(Submission.total)).where(
                ~exists().where(PendingDeletion.id == Submission.id)
            )
            if exam_id:
                statement = statement.where(Submission.exam_id == exam_id)
            groups = db.execute(statement.group_by(Submission.status)).all()
            counts = {status: count for status, count, average in groups}
            return {
                "exams": db.scalar(select(func.count()).select_from(Exam)),
                "submissions": sum(counts.values()),
                "approved": counts.get("approved", 0),
                "review": counts.get("review", 0) + counts.get("failed", 0),
                "average_total": next(
                    (average for status, count, average in groups if status == "approved"), None
                ),
            }

    def submissions(self, exam_id=None, *, limit=None, offset=0, status=None):
        with self.sessions() as db:
            stmt = (
                select(Submission)
                .where(~exists().where(PendingDeletion.id == Submission.id))
                .order_by(Submission.created_at.desc(), Submission.id)
            )
            if exam_id:
                stmt = stmt.where(Submission.exam_id == exam_id)
            if status:
                stmt = stmt.where(Submission.status == status)
            if limit is not None:
                stmt = stmt.limit(limit).offset(offset)
            return list(db.scalars(stmt))

    def submission(self, submission_id):
        with self.sessions() as db:
            if db.get(PendingDeletion, submission_id):
                raise ValueError("التسليم قيد الحذف")
            return self._get(db, submission_id)

    def image_path(self, name):
        path = (self.images / name).resolve()
        if path.parent != self.images.resolve() or path.suffix != ".png":
            raise ValueError("مسار صورة غير صالح")
        return path

    def storage_bytes(self):
        with self.sessions() as db:
            assets = db.scalar(select(func.coalesce(func.sum(FileAsset.size), 0)))
        return assets + sum(
            path.stat().st_size for path in self.root.glob("smartgrade.sqlite3*") if path.is_file()
        )

    def _save_image(self, image):
        name = f"{uuid4().hex}.png"
        self.image_path(name).write_bytes(png_bytes(image))
        return name

    @bounded_processing
    def upload(
        self,
        exam_id,
        student_code,
        raw,
        blank_threshold=0.12,
        marked_threshold=0.55,
        *,
        max_storage_bytes=None,
        audit=None,
        request_key=None,
        fingerprint=None,
    ):
        if not student_code.strip() or len(student_code) > 100:
            raise ValueError("أدخل رمز طالب من 1 إلى 100 حرف")
        spec = ExamSpec.model_validate(self.exam(exam_id).spec)
        started = perf_counter()
        image = decode_image(raw)
        aligned_name, image_name = None, None
        original, current = {}, {}
        # Fail closed on alignment errors. Do not create a misleading zero-score record.
        aligned = None
        if spec.kind == "mcq":
            aligned, readings = read_answers(image, blank_threshold, marked_threshold)
            original = {"readings": readings, "thresholds": [blank_threshold, marked_threshold]}
            current = {"answers": [r["answer"] for r in readings]}
        if max_storage_bytes is not None:
            current_bytes = self.storage_bytes()
            needed = len(png_bytes(image)) + (len(png_bytes(aligned)) if aligned is not None else 0) + 65536
            if current_bytes + needed > max_storage_bytes:
                raise ValueError("مساحة المؤسسة لا تكفي لحفظ هذه الورقة")
        try:
            image_name = self._save_image(image)
            if aligned is not None:
                aligned_name = self._save_image(aligned)
            with self.sessions.begin() as db:
                for name in (image_name, aligned_name):
                    if name:
                        db.add(FileAsset(name="images/" + name, size=self.image_path(name).stat().st_size))
                row = Submission(
                    exam_id=exam_id,
                    student_code=student_code.strip(),
                    image_name=image_name,
                    aligned_name=aligned_name,
                    original=original,
                    current=current,
                    processing_seconds=perf_counter() - started,
                )
                db.add(row)
                db.flush()
                if request_key:
                    db.add(RequestReceipt(key=request_key, fingerprint=fingerprint, submission_id=row.id))
                self._event(db, audit, row.id)
                db.add(
                    ReviewHistory(
                        submission_id=row.id,
                        action="uploaded",
                        reviewer="local",
                        before={},
                        after=self._snapshot(row),
                        reason="",
                    )
                )
                return row.id
        except Exception:
            for name in [image_name, aligned_name]:
                if name:
                    try:
                        self.image_path(name).unlink(missing_ok=True)
                    except OSError:
                        logging.getLogger(__name__).warning("upload_file_cleanup_deferred")
            raise

    def _get(self, db, sid, expected_revision=None, *, allow_deleting=False):
        row = db.get(Submission, sid)
        if row is None:
            raise ValueError("التسليم غير موجود")
        if not allow_deleting and db.get(PendingDeletion, sid):
            raise RevisionConflict("التسليم قيد الحذف")
        if expected_revision is not None and expected_revision != row.revision:
            raise RevisionConflict("تم تعديل التسليم في جلسة أخرى؛ أعد تحميل الصفحة")
        return row

    @staticmethod
    def _snapshot(row):
        return deepcopy(
            {
                "status": row.status,
                "text": row.text,
                "original": row.original,
                "current": row.current,
                "proposal": row.proposal,
                "approved": row.approved,
                "total": row.total,
            }
        )

    @staticmethod
    def _invalidate(row):
        row.status, row.approved, row.total = "review", None, None

    def _history(self, db, row, before, action, reviewer, reason="", *, expire_reports=True):
        if not reviewer.strip() or len(reviewer) > 100 or len(reason) > 1000:
            raise ValueError("اسم المراجع أو سبب التعديل غير صالح")
        row.updated_at = now()
        if expire_reports:
            self._expire_reports(db, row.exam_id)
        db.add(
            ReviewHistory(
                submission_id=row.id,
                action=action,
                reviewer=reviewer.strip(),
                before=before,
                after=self._snapshot(row),
                reason=reason,
            )
        )

    @staticmethod
    def _expire_reports(db, exam_id):
        for job in db.scalars(
            select(Job).where(
                Job.kind == "report", Job.state == "succeeded", Job.payload["exam_id"].as_string() == exam_id
            )
        ):
            job.state = "expired"

    def save_text(self, sid, text, reviewer, revision, source="manual", *, audit=None):
        if len(text) > 30000:
            raise ValueError("النص يتجاوز الحد الأقصى")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            if db.get(Exam, row.exam_id).kind != "essay":
                raise ValueError("هذا التسليم ليس مقاليًا")
            if row.text == text:
                return False
            before = self._snapshot(row)
            if source == "ocr":
                row.original = {
                    **row.original,
                    "ocr_text": row.original.get("ocr_text", text),
                    "ocr_provider": "tesseract",
                    "ocr_runs": [*row.original.get("ocr_runs", []), {"text": text, "at": now()}],
                    "ocr_failed": False,
                    "processing_status": {**row.original.get("processing_status", {}),
                        "ocr": {"state": "succeeded", "code": "ok", "input_revision": revision, "at": now()}},
                }
            row.text, row.proposal = text, None
            row.current = {k: v for k, v in row.current.items() if k != "essay_answers"}
            self._invalidate(row)
            self._history(db, row, before, "ocr_saved" if source == "ocr" else "text_corrected", reviewer)
            self._event(db, audit, sid)
            return True

    def save_answers(self, sid, answers, reviewer, revision, *, audit=None):
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            spec = ExamSpec.model_validate(db.get(Exam, row.exam_id).spec)
            answers = validate_answers(spec, answers)
            if row.current.get("essay_answers") == answers:
                return False
            before = self._snapshot(row)
            row.current = {**row.current, "essay_answers": answers}
            row.proposal = None
            self._invalidate(row)
            self._history(db, row, before, "essay_answers_changed", reviewer)
            self._event(db, audit, sid)
            return True

    def save_proposal(self, sid, proposal, revision, *, audit=None):
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            spec = ExamSpec.model_validate(db.get(Exam, row.exam_id).spec)
            parsed, _ = parse_essay_grade(proposal, spec, row.text, row.current)
            before = self._snapshot(row)
            row.proposal = parsed.model_dump()
            row.original = {**row.original, "llm_failed": False, "processing_status": {
                **row.original.get("processing_status", {}),
                "llm": {"state": "succeeded", "code": "ok", "input_revision": revision, "at": now()},
            }}
            self._invalidate(row)
            self._history(db, row, before, "ai_proposal", "ai-provider")
            self._event(db, audit, sid)

    def approve_mcq(self, sid, answers, reviewer, reason, revision, reviewed, *, audit=None):
        if not reviewed:
            raise ValueError("أكد مراجعة الحالات غير الواضحة والفارغة والمتعددة")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            spec = ExamSpec.model_validate(db.get(Exam, row.exam_id).spec)
            if spec.kind != "mcq":
                raise ValueError("التسليم ليس اختيارًا من متعدد")
            total = score_answers(answers, spec.answer_key, spec.points_per_question)
            before = self._snapshot(row)
            row.current = {"answers": answers}
            row.approved, row.total, row.status = {"answers": answers}, total, "approved"
            self._history(db, row, before, "mcq_approved", reviewer, reason)
            self._event(db, audit, sid)

    def approve_essay(self, sid, proposal, reviewer, reason, revision, reviewed, *, audit=None):
        if not reviewed:
            raise ValueError("تجب مراجعة النص والمعايير قبل الاعتماد")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            spec = ExamSpec.model_validate(db.get(Exam, row.exam_id).spec)
            parsed, total = parse_essay_grade(proposal, spec, row.text, row.current)
            before = self._snapshot(row)
            row.approved, row.total, row.status = parsed.model_dump(), total, "approved"
            self._history(db, row, before, "essay_approved", reviewer, reason)
            self._event(db, audit, sid)

    def history(self, sid):
        with self.sessions() as db:
            return list(
                db.scalars(
                    select(ReviewHistory)
                    .where(ReviewHistory.submission_id == sid)
                    .order_by(ReviewHistory.created_at)
                )
            )

    def delete_submission(self, sid, revision, *, audit=None):
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision, allow_deleting=True)
            if not db.get(PendingDeletion, sid):
                db.add(
                    PendingDeletion(
                        id=sid, names=[n for n in [row.image_name, row.aligned_name] if n], audit=audit
                    )
                )
                self._event(db, {**audit, "action": "submission.delete_requested"} if audit else None, sid)
                self._expire_reports(db, row.exam_id)
        return self.finish_deletion(sid)

    def finish_deletion(self, sid):
        with self.sessions() as db:
            pending = db.get(PendingDeletion, sid)
            if not pending:
                return True
        failed = False
        for name in pending.names:
            try:
                self.image_path(name).unlink(missing_ok=True)
            except OSError:
                failed = True
        with self.sessions.begin() as db:
            row = db.get(PendingDeletion, sid)
            if failed:
                row.attempts += 1
                return False
            submission = db.get(Submission, sid)
            if submission:
                db.delete(submission)
            for name in row.names:
                asset = db.get(FileAsset, "images/" + name)
                if asset:
                    db.delete(asset)
            self._event(db, row.audit, sid)
            db.delete(row)
        return True

    def retry_deletions(self, limit=100):
        with self.sessions() as db:
            ids = list(db.scalars(select(PendingDeletion.id).limit(limit)))
        return sum(self.finish_deletion(sid) for sid in ids)

    def record_processing_failure(self, sid, stage, revision, *, code=None, audit=None):
        from modules.processing import safe_code
        if stage not in {"ocr", "llm"}:
            raise ValueError("مرحلة غير صالحة")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            before = self._snapshot(row)
            status = {**row.original.get("processing_status", {})}
            status[stage] = {"state": "failed", "code": safe_code(code or ("provider_failed" if stage == "llm" else "ocr_failed")),
                             "input_revision": revision, "at": now()}
            row.original = {**row.original, stage + "_failed": True, "processing_status": status}
            # An unsuccessful attempt changed neither the saved answers nor the approved grade.
            self._history(db, row, before, stage + "_failed", "system",
                          "لم تكتمل المعالجة؛ احتُفظ بالمدخلات والدرجات المحفوظة", expire_reports=False)
            self._event(db, audit, sid)
            db.flush()
            return row.revision
