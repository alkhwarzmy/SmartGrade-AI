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


class EssayQuestion(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=10000)
    model_answer: str = Field(default="", max_length=20000)
    instructions: str = Field(default="", max_length=5000)
    maximum: float = Field(gt=0, le=20000)
    criteria: list[Criterion] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_question(self):
        if not self.id.strip() or not self.question.strip():
            raise ValueError("معرف السؤال ونصه مطلوبان")
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("معرفات المعايير يجب أن تكون فريدة داخل السؤال")
        if abs(sum(c.maximum for c in self.criteria) - self.maximum) > 1e-6:
            raise ValueError("مجموع حدود المعايير لا يساوي درجة السؤال")
        return self


class ExamSpec(StrictModel):
    title: str = Field(min_length=1, max_length=150)
    kind: Literal["mcq", "essay"]
    answer_key: list[Literal["A", "B", "C", "D"]] = Field(default_factory=list)
    points_per_question: float = Field(default=1, gt=0, le=1000)
    question: str = Field(default="", max_length=10000)
    model_answer: str = Field(default="", max_length=20000)
    instructions: str = Field(default="", max_length=5000)
    maximum: float = Field(default=20, gt=0)
    criteria: list[Criterion] = Field(default_factory=list, max_length=30)
    questions: list[EssayQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_exam(self):
        if not self.title.strip():
            raise ValueError("عنوان الاختبار مطلوب")
        if self.kind == "mcq":
            if self.questions or len(self.answer_key) != 20:
                raise ValueError("قالب OMR يتطلب 20 إجابة دون أسئلة مقالية")
            self.maximum = 20 * self.points_per_question
        elif self.questions:
            if self.question or self.model_answer or self.criteria:
                raise ValueError("استخدم قائمة الأسئلة أو صيغة السؤال القديم دون الجمع بينهما")
            if len({q.id for q in self.questions}) != len(self.questions):
                raise ValueError("معرفات الأسئلة يجب أن تكون فريدة")
            self.maximum = sum(q.maximum for q in self.questions)
        else:
            if self.maximum > 20000 or not self.question.strip() or not self.criteria:
                raise ValueError("السؤال ومعايير التصحيح ودرجة صحيحة مطلوبون")
            if len({c.id for c in self.criteria}) != len(self.criteria):
                raise ValueError("معرفات المعايير يجب أن تكون فريدة")
            if abs(sum(c.maximum for c in self.criteria) - self.maximum) > 1e-6:
                raise ValueError("مجموع حدود المعايير لا يساوي الدرجة الكاملة")
        return self

    def essay_questions(self):
        if self.kind != "essay":
            raise ValueError("الاختبار ليس مقاليًا")
        return self.questions or [EssayQuestion(
            id="q1", question=self.question, model_answer=self.model_answer,
            instructions=self.instructions, maximum=self.maximum, criteria=self.criteria,
        )]


class CriterionGrade(StrictModel):
    criterion_id: str
    score: float = Field(ge=0)
    evidence: str = Field(max_length=10000)
    rationale: str = Field(min_length=1, max_length=5000)
    missing: str = Field(max_length=5000)


class GradeProposal(StrictModel):
    criteria: list[CriterionGrade] = Field(min_length=1, max_length=30)
    needs_review: Literal[True] = True


class QuestionGrade(GradeProposal):
    question_id: str = Field(min_length=1, max_length=40)


class EssayGradeProposal(StrictModel):
    questions: list[QuestionGrade] = Field(min_length=1)
    needs_review: Literal[True] = True


def validate_grade(proposal: GradeProposal, spec: ExamSpec | EssayQuestion, text: str) -> float:
    if isinstance(spec, ExamSpec) and (spec.kind != "essay" or spec.questions):
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


def answer_texts(spec, text, current):
    questions = spec.essay_questions()
    saved = current.get("essay_answers", {})
    return {q.id: saved.get(q.id, text if len(questions) == 1 else "") for q in questions}


def validate_answers(spec, answers):
    if not spec.questions or set(answers) != {q.id for q in spec.questions}:
        raise ValueError("الإجابات يجب أن تطابق جميع أسئلة الاختبار")
    if any(not isinstance(t, str) or len(t) > 30000 for t in answers.values()):
        raise ValueError("كل إجابة يجب أن تكون نصًا لا يتجاوز 30000 حرف")
    if sum(len(t) for t in answers.values()) > 30000:
        raise ValueError("مجموع نصوص إجابات الورقة يتجاوز 30000 حرف")
    return dict(answers)


def parse_essay_grade(value, spec, text, current):
    if not spec.questions:
        parsed = GradeProposal.model_validate(value)
        if not text.strip():
            raise ValueError("نص الإجابة مطلوب قبل الاعتماد")
        return parsed, validate_grade(parsed, spec, text)
    if len(spec.questions) > 1 and not isinstance(current.get("essay_answers"), dict):
        raise ValueError("احفظ توزيع إجابات الأسئلة قبل التصحيح أو الاعتماد")
    parsed = EssayGradeProposal.model_validate(value)
    questions = {q.id: q for q in spec.questions}
    ids = [g.question_id for g in parsed.questions]
    if len(ids) != len(set(ids)) or set(ids) != set(questions):
        raise ValueError("التصحيح يجب أن يشمل جميع الأسئلة مرة واحدة")
    answers = validate_answers(spec, answer_texts(spec, text, current))
    total = 0.0
    for grade in parsed.questions:
        answer = answers[grade.question_id]
        if not answer.strip() and any(c.score != 0 or c.evidence for c in grade.criteria):
            raise ValueError("السؤال دون إجابة يجب أن يحصل على صفر دون دليل مختلق")
        total += validate_grade(GradeProposal(criteria=grade.criteria), questions[grade.question_id], answer)
    return parsed, round(total, 6)
