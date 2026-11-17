from copy import deepcopy
import json
import httpx
import pytest
from pydantic import ValidationError
from modules.schemas import GradeProposal, validate_grade, ExamSpec, Criterion
from modules.essay_grader import propose_grade, ProviderError
from modules.review import Store
from modules.preprocessing import png_bytes
from modules.mcq_grader import template


@pytest.mark.parametrize("change", ["over", "negative", "nan", "evidence", "missing", "duplicate", "final"])
def test_invalid_grades_rejected(essay_spec, valid_proposal, change):
    value = deepcopy(valid_proposal)
    if change == "over":
        value["criteria"][0]["score"] = 5
    elif change == "negative":
        value["criteria"][0]["score"] = -1
    elif change == "nan":
        value["criteria"][0]["score"] = float("nan")
    elif change == "evidence":
        value["criteria"][0]["evidence"] = "نص مختلق"
    elif change == "missing":
        value["criteria"][0]["criterion_id"] = "unknown"
    elif change == "duplicate":
        value["criteria"].append(value["criteria"][0])
    else:
        value["needs_review"] = False
    with pytest.raises((ValueError, ValidationError)):
        validate_grade(GradeProposal.model_validate(value), essay_spec, "الماء مهم")


def test_rubric_totals_and_ids():
    with pytest.raises(ValueError):
        ExamSpec(
            title="x",
            kind="essay",
            question="q",
            maximum=3,
            criteria=[Criterion(id="a", description="a", maximum=2)],
        )


def test_essay_lifecycle_and_invalidation(tmp_path, essay_spec, valid_proposal):
    store = Store(tmp_path)
    eid = store.create_exam(essay_spec)
    sid = store.upload(eid, "طالب", png_bytes(template()))
    store.save_text(sid, "الماء مهم", "معلم", 1)
    row = store.submission(sid)
    store.save_proposal(sid, valid_proposal, row.revision)
    row = store.submission(sid)
    assert row.status == "review" and row.total is None
    store.approve_essay(sid, valid_proposal, "معلم", "تم التدقيق", row.revision, True)
    row = store.submission(sid)
    assert row.total == 3 and row.status == "approved"
    store.save_text(sid, "نص جديد", "معلم", row.revision)
    row = store.submission(sid)
    assert row.total is None and row.approved is None and row.proposal is None
    assert row.status == "review" and len(store.history(sid)) == 5


def configured(monkeypatch):
    for name, value in {
        "ALLOW_EXTERNAL_AI": "true",
        "ALLOW_PAID_API": "true",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
    }.items():
        monkeypatch.setenv(name, value)


def test_provider_opt_in(essay_spec, monkeypatch):
    configured(monkeypatch)
    with pytest.raises(ProviderError):
        propose_grade(essay_spec, "الماء مهم", consent=False)
    monkeypatch.setenv("ALLOW_EXTERNAL_AI", "false")
    with pytest.raises(ProviderError):
        propose_grade(essay_spec, "الماء مهم", consent=True)


def test_adapter_structured_contract(essay_spec, valid_proposal, monkeypatch):
    configured(monkeypatch)

    def handler(request):
        payload = json.loads(request.content)
        assert "student_answer" in payload["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid_proposal)}}]})

    result = propose_grade(essay_spec, "الماء مهم", consent=True, transport=httpx.MockTransport(handler))
    assert result.criteria[0].score == 3


@pytest.mark.parametrize("mode", ["bad_json", "timeout", "bad_grade", "http_error"])
def test_provider_failure_is_not_a_grade(essay_spec, valid_proposal, monkeypatch, mode):
    configured(monkeypatch)

    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if mode == "http_error":
            return httpx.Response(500)
        if mode == "bad_json":
            return httpx.Response(200, text="bad")
        valid_proposal["criteria"][0]["score"] = 99
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid_proposal)}}]})

    with pytest.raises(ProviderError):
        propose_grade(essay_spec, "الماء مهم", consent=True, transport=httpx.MockTransport(handler))
