"""Application service boundary. UI cannot bypass grade/state validation."""

from copy import deepcopy
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from sqlalchemy import select
from database.db import make_engine, session_factory
from database.models import Exam, Submission, ReviewHistory, now
from modules.schemas import ExamSpec, GradeProposal, validate_grade
from modules.preprocessing import decode_image, png_bytes
from modules.mcq_grader import read_answers, score_answers


class RevisionConflict(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.images = self.root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(self.root)
        self.sessions = session_factory(self.engine)

    def create_exam(self, spec: ExamSpec) -> str:
        spec = ExamSpec.model_validate(spec.model_dump())
        with self.sessions.begin() as db:
            row = Exam(title=spec.title, kind=spec.kind, spec=spec.model_dump())
            db.add(row)
            db.flush()
            return row.id

    def exams(self):
        with self.sessions() as db:
            return list(db.scalars(select(Exam).order_by(Exam.created_at.desc())))

    def exam(self, exam_id):
        with self.sessions() as db:
            exam = db.get(Exam, exam_id)
            if not exam:
                raise ValueError("الاختبار غير موجود")
            return exam

    def submissions(self, exam_id=None):
        with self.sessions() as db:
            stmt = select(Submission).order_by(Submission.created_at.desc())
            if exam_id:
                stmt = stmt.where(Submission.exam_id == exam_id)
            return list(db.scalars(stmt))

    def submission(self, submission_id):
        with self.sessions() as db:
            return self._get(db, submission_id)

    def image_path(self, name):
        path = (self.images / name).resolve()
        if path.parent != self.images.resolve() or path.suffix != ".png":
            raise ValueError("مسار صورة غير صالح")
        return path

    def _save_image(self, image):
        name = f"{uuid4().hex}.png"
        self.image_path(name).write_bytes(png_bytes(image))
        return name

    def upload(
        self,
        exam_id,
        student_code,
        raw,
        blank_threshold=0.12,
        marked_threshold=0.55,
        *,
        max_storage_bytes=None,
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
            current_bytes = sum(
                p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()
            )
            needed = len(png_bytes(image)) + (len(png_bytes(aligned)) if aligned is not None else 0) + 65536
            if current_bytes + needed > max_storage_bytes:
                raise ValueError("مساحة المؤسسة لا تكفي لحفظ هذه الورقة")
        try:
            image_name = self._save_image(image)
            if aligned is not None:
                aligned_name = self._save_image(aligned)
            with self.sessions.begin() as db:
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
                    self.image_path(name).unlink(missing_ok=True)
            raise

    def _get(self, db, sid, expected_revision=None):
        row = db.get(Submission, sid)
        if row is None:
            raise ValueError("التسليم غير موجود")
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

    def _history(self, db, row, before, action, reviewer, reason=""):
        if not reviewer.strip() or len(reviewer) > 100 or len(reason) > 1000:
            raise ValueError("اسم المراجع أو سبب التعديل غير صالح")
        row.updated_at = now()
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

    def save_text(self, sid, text, reviewer, revision, source="manual"):
        if len(text) > 30000:
            raise ValueError("النص يتجاوز الحد الأقصى")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            if db.get(Exam, row.exam_id).kind != "essay":
                raise ValueError("هذا التسليم ليس مقاليًا")
            before = self._snapshot(row)
            if source == "ocr":
                row.original = {
                    **row.original,
                    "ocr_text": row.original.get("ocr_text", text),
                    "ocr_provider": "tesseract",
                    "ocr_runs": [*row.original.get("ocr_runs", []), {"text": text, "at": now()}],
                }
            row.text, row.proposal = text, None
            self._invalidate(row)
            self._history(db, row, before, "ocr_saved" if source == "ocr" else "text_corrected", reviewer)

    def save_proposal(self, sid, proposal, revision):
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            spec = ExamSpec.model_validate(db.get(Exam, row.exam_id).spec)
            parsed = GradeProposal.model_validate(proposal)
            validate_grade(parsed, spec, row.text)
            before = self._snapshot(row)
            row.proposal = parsed.model_dump()
            self._invalidate(row)
            self._history(db, row, before, "ai_proposal", "ai-provider")

    def approve_mcq(self, sid, answers, reviewer, reason, revision, reviewed):
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

    def approve_essay(self, sid, proposal, reviewer, reason, revision, reviewed):
        if not reviewed:
            raise ValueError("تجب مراجعة النص والمعايير قبل الاعتماد")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            spec = ExamSpec.model_validate(db.get(Exam, row.exam_id).spec)
            if not row.text.strip():
                raise ValueError("نص الإجابة مطلوب قبل الاعتماد")
            parsed = GradeProposal.model_validate(proposal)
            total = validate_grade(parsed, spec, row.text)
            before = self._snapshot(row)
            row.approved, row.total, row.status = parsed.model_dump(), total, "approved"
            self._history(db, row, before, "essay_approved", reviewer, reason)

    def history(self, sid):
        with self.sessions() as db:
            return list(
                db.scalars(
                    select(ReviewHistory)
                    .where(ReviewHistory.submission_id == sid)
                    .order_by(ReviewHistory.created_at)
                )
            )

    def delete_submission(self, sid, revision):
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            names = [row.image_name, row.aligned_name]
            db.delete(row)
        for name in names:
            if name:
                self.image_path(name).unlink(missing_ok=True)

    def record_processing_failure(self, sid, stage, revision):
        if stage not in {"ocr", "llm"}:
            raise ValueError("مرحلة غير صالحة")
        with self.sessions.begin() as db:
            row = self._get(db, sid, revision)
            before = self._snapshot(row)
            row.original = {**row.original, stage + "_failed": True}
            self._invalidate(row)
            self._history(db, row, before, stage + "_failed", "system", "يلزم التصحيح أو الإدخال اليدوي")
