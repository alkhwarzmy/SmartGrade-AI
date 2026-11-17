"""SQLite persistence with constraints and append-only review snapshots."""

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import CheckConstraint, ForeignKey, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(10))
    spec: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)
    __table_args__ = (CheckConstraint("kind IN ('mcq','essay')"),)


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="RESTRICT"), index=True)
    student_code: Mapped[str] = mapped_column(String(100))
    image_name: Mapped[str] = mapped_column(String(40))
    aligned_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(default="review", index=True)
    original: Mapped[dict] = mapped_column(JSON, default=dict)
    current: Mapped[dict] = mapped_column(JSON, default=dict)
    text: Mapped[str] = mapped_column(Text, default="")
    proposal: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    approved: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    total: Mapped[float | None] = mapped_column(nullable=True)
    revision: Mapped[int] = mapped_column(default=1)
    processing_seconds: Mapped[float] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)
    __table_args__ = (
        CheckConstraint("status IN ('review','approved','failed')"),
        CheckConstraint("total IS NULL OR total >= 0"),
        CheckConstraint("revision >= 1"),
        CheckConstraint(
            "(status = 'approved' AND approved IS NOT NULL AND total IS NOT NULL) OR "
            "(status != 'approved' AND approved IS NULL AND total IS NULL)"
        ),
    )
    __mapper_args__ = {"version_id_col": revision}


class ReviewHistory(Base):
    __tablename__ = "review_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    reviewer: Mapped[str] = mapped_column(String(100))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[str] = mapped_column(default=now)
