"""Validated contracts shared by UI, storage and AI adapters."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CHOICES = ("A", "B", "C", "D")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Criterion(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=1000)
    maximum: float = Field(gt=0, le=1000)


class ExamSpec(StrictModel):
    title: str = Field(min_length=1, max_length=150)
    kind: Literal["mcq", "essay"]
    answer_key: list[Literal["A", "B", "C", "D"]] = Field(default_factory=list)
    points_per_question: float = Field(default=1, gt=0, le=1000)
    question: str = Field(default="", max_length=10000)
    model_answer: str = Field(default="", max_length=20000)
    instructions: str = Field(default="", max_length=5000)
    maximum: float = Field(default=20, gt=0, le=20000)
    criteria: list[Criterion] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid_exam(self):
        if not self.title.strip():
            raise ValueError("عنوان الاختبار مطلوب")
        if self.kind == "mcq":
            if len(self.answer_key) != 20:
                raise ValueError("قالب OMR يتطلب 20 إجابة")
            self.maximum = 20 * self.points_per_question
        else:
            if not self.question.strip() or not self.criteria:
                raise ValueError("السؤال ومعايير التصحيح مطلوبان")
            if len({c.id for c in self.criteria}) != len(self.criteria):
                raise ValueError("معرفات المعايير يجب أن تكون فريدة")
            if abs(sum(c.maximum for c in self.criteria) - self.maximum) > 1e-6:
                raise ValueError("مجموع حدود المعايير لا يساوي الدرجة الكاملة")
        return self


class CriterionGrade(StrictModel):
    criterion_id: str
    score: float = Field(ge=0)
    evidence: str = Field(max_length=10000)
    rationale: str = Field(min_length=1, max_length=5000)
    missing: str = Field(max_length=5000)


class GradeProposal(StrictModel):
    criteria: list[CriterionGrade] = Field(min_length=1, max_length=30)
    needs_review: Literal[True] = True


def validate_grade(proposal: GradeProposal, spec: ExamSpec, text: str) -> float:
    if spec.kind != "essay":
        raise ValueError("معايير مقالية مطلوبة")
    rubric = {c.id: c for c in spec.criteria}
    ids = [g.criterion_id for g in proposal.criteria]
    if len(ids) != len(set(ids)) or set(ids) != set(rubric):
        raise ValueError("المعايير المستلمة لا تطابق السؤال")
    for grade in proposal.criteria:
        if grade.score > rubric[grade.criterion_id].maximum:
            raise ValueError("درجة خارج حدود المعيار")
        if grade.evidence and grade.evidence not in text:
            raise ValueError("الدليل غير موجود في نص الإجابة")
    total = round(sum(g.score for g in proposal.criteria), 6)
    if total > spec.maximum + 1e-6:
        raise ValueError("الدرجة تتجاوز الحد الأعلى")
    return total
