"""Whole-paper multi-question grading, legacy reads and per-question evidence."""
from copy import deepcopy
import io
import json
import httpx
from openpyxl import load_workbook
import pytest
from modules.schemas import ExamSpec, EssayQuestion, Criterion, parse_essay_grade
from modules.review import Store, RevisionConflict
from modules.preprocessing import png_bytes
from modules.mcq_grader import template
from modules.reports import frames, excel_bytes
from modules.essay_grader import propose_grade, ProviderError


@pytest.fixture
def multi():
    return ExamSpec(title="متعدد", kind="essay", questions=[
        EssayQuestion(id=f"q{i}", question=f"السؤال {i}", maximum=m,
                      criteria=[Criterion(id="c1", description="الفكرة", maximum=m)])
        for i, m in enumerate([4, 6, 5], 1)
    ])


@pytest.fixture
def answers():
    return {"q1": "الماء مهم", "q2": "الشمس مفيدة", "q3": ""}


@pytest.fixture
def grade():
    return {"questions": [{"question_id": q, "criteria": [{
        "criterion_id": "c1", "score": s, "evidence": e, "rationale": "شرح جزئي" if s else "لم يجب",
        "missing": "",
    }]} for q, s, e in [("q1", 3, "الماء"), ("q2", 2, "الشمس"), ("q3", 0, "")]]}


def test_more_than_one_hundred_questions_can_be_created_and_graded(multi):
    questions = [q.model_copy(update={"id": f"q{i}"}) for i, q in enumerate(multi.questions * 40)]
    spec = ExamSpec(title="120", kind="essay", questions=questions)
    spec = ExamSpec.model_validate_json(spec.model_dump_json())
    proposal = {"questions": [{"question_id": q.id, "criteria": [{
        "criterion_id": "c1", "score": 3, "evidence": "الماء", "rationale": "شرح", "missing": "",
    }]} for q in questions]}
    _, total = parse_essay_grade(proposal, spec, "", {"essay_answers": {q.id: "الماء" for q in questions}})
    assert len(spec.questions) == 120 and spec.maximum == 600 and total == 360


@pytest.mark.parametrize("change", ["duplicate_id", "mixed", "rubric", "empty", "duplicate_criterion"])
def test_invalid_specs(multi, change):
    data = multi.model_dump()
    if change == "duplicate_id":
        data["questions"][1]["id"] = "q1"
    elif change == "mixed":
        data["question"] = "قديم"
    elif change == "rubric":
        data["questions"][0]["maximum"] = 9
    elif change == "empty":
        data["questions"] = []
    else:
        data["questions"][0]["criteria"] *= 2
    with pytest.raises(ValueError):
        ExamSpec.model_validate(data)


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign", "evidence", "over", "blank", "legacy"])
def test_invalid_question_grades(multi, grade, answers, change):
    value = deepcopy(grade)
    if change == "missing":
        value["questions"].pop()
    elif change == "duplicate":
        value["questions"].append(value["questions"][0])
    elif change == "foreign":
        value["questions"][0]["question_id"] = "alien"
    elif change == "evidence":
        value["questions"][0]["criteria"][0]["evidence"] = "الشمس"
    elif change == "over":
        value["questions"][0]["criteria"][0]["score"] = 5
    elif change == "blank":
        value["questions"][2]["criteria"][0]["score"] = 1
    else:
        value = {"criteria": value["questions"][0]["criteria"]}
    with pytest.raises(ValueError):
        parse_essay_grade(value, multi, "الماء الشمس", {"essay_answers": answers})


def test_lifecycle_exports_noop_and_conflict(tmp_path, multi, answers, grade):
    store = Store(tmp_path)
    eid = store.create_exam(multi)
    sid = store.upload(eid, "SYNTHETIC", png_bytes(template()))
    with pytest.raises(ValueError):
        store.save_answers(sid, {"q1": "فقط"}, "teacher", 1)
    store.save_answers(sid, answers, "teacher", 1)
    row = store.submission(sid)
    store.save_proposal(sid, grade, row.revision)
    row = store.submission(sid)
    store.approve_essay(sid, grade, "teacher", "", row.revision, True)
    row = store.submission(sid)
    assert row.total == 5 and row.status == "approved"
    summary, detail = frames(store, eid)
    assert summary.iloc[0]["الدرجة الكاملة"] == 15 and detail["الدرجة"].sum() == 5
    assert list(detail["معرف السؤال"]) == ["q1", "q2", "q3"]
    book = load_workbook(io.BytesIO(excel_bytes(summary, detail)))
    assert book["درجات الأسئلة"].max_row == 4
    revision = row.revision
    assert store.save_answers(sid, answers, "teacher", revision) is False
    assert store.submission(sid).total == 5 and store.submission(sid).revision == revision
    store.save_answers(sid, {**answers, "q2": "إجابة جديدة"}, "teacher", revision)
    updated = store.submission(sid)
    assert updated.total is None and updated.approved is None and updated.proposal is None
    with pytest.raises(RevisionConflict):
        store.save_answers(sid, answers, "other", revision)
    store.save_text(sid, "مصدر جديد", "teacher", updated.revision, source="ocr")
    assert "essay_answers" not in store.submission(sid).current


def test_multi_provider_one_request_scoped_answers(monkeypatch, multi, answers, grade):
    for key, value in {"ALLOW_EXTERNAL_AI": "true", "ALLOW_PAID_API": "true",
                       "LLM_API_KEY": "synthetic", "LLM_MODEL": "synthetic"}.items():
        monkeypatch.setenv(key, value)
    requests = []
    def handler(request):
        requests.append(request)
        user = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert len(user["questions"]) == 3
        assert user["questions"][1]["student_answer"] == answers["q2"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(grade)}}]})
    assert len(propose_grade(multi, "", current={"essay_answers": answers}, consent=True,
                            transport=httpx.MockTransport(handler)).questions) == 3
    assert len(requests) == 1
    grade["questions"][0]["criteria"][0]["evidence"] = "الشمس"
    with pytest.raises(ProviderError):
        propose_grade(multi, "", current={"essay_answers": answers}, consent=True,
                      transport=httpx.MockTransport(handler))
