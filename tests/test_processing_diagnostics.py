"""Actionable failure reasons, preserved approvals and privacy-safe diagnosis."""
from copy import deepcopy
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from api import create_app
from modules.schemas import ExamSpec, EssayQuestion, Criterion, parse_essay_grade
from modules.workspace import Workspace
from modules.grading_diagnostics import essay_diagnostics
from modules.essay_grader import propose_grade, ProviderError, provider_status
from modules.preprocessing import png_bytes
from modules.mcq_grader import template
from modules.jobs import Jobs
from scripts.worker import cycle


def enable(monkeypatch):
    for key, value in {"ALLOW_EXTERNAL_AI": "true", "ALLOW_PAID_API": "true",
                       "LLM_API_KEY": "SYNTHETIC-SECRET-NOT-EXPORTED", "LLM_MODEL": "synthetic"}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("status,error_code,expected", [
    (401, "", "provider_auth"), (403, "", "provider_auth"),
    (429, "insufficient_quota", "provider_quota"), (429, "", "provider_rate_limit"),
    (500, "", "provider_unavailable"), (400, "context_length_exceeded", "provider_context_limit"),
    (400, "unsupported_parameter", "provider_parameters"),
    (404, "model_not_found", "provider_model_unavailable"), (404, "", "provider_endpoint"),
])
def test_provider_failure_codes_are_redacted_and_never_retried(monkeypatch, essay_spec, status, error_code, expected):
    enable(monkeypatch)
    calls = []
    secret = "PRIVATE-STUDENT-TEXT-AND-UPSTREAM-ERROR"
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": error_code, "message": secret}})
    with pytest.raises(ProviderError) as error:
        propose_grade(essay_spec, secret, consent=True, transport=httpx.MockTransport(handler))
    assert error.value.code == expected and error.value.attempted
    assert secret not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize("mode,expected", [
    ("timeout", "provider_timeout"), ("truncated", "provider_truncated"),
    ("duplicate_json", "provider_invalid_response"), ("no_evidence", "provider_missing_evidence"),
    ("wrong_evidence", "provider_evidence"), ("over", "provider_invalid_grade"),
])
def test_invalid_or_incomplete_proposals_are_not_saved(monkeypatch, essay_spec, valid_proposal, mode, expected):
    enable(monkeypatch)
    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("do not expose this", request=request)
        grade = deepcopy(valid_proposal)
        if mode == "no_evidence":
            grade["criteria"][0]["evidence"] = ""
        if mode == "wrong_evidence":
            grade["criteria"][0]["evidence"] = "نص لم يكتبه الطالب"
        if mode == "over":
            grade["criteria"][0]["score"] = 99
        content = '{"criteria": [], "criteria": []}' if mode == "duplicate_json" else json.dumps(grade)
        return httpx.Response(200, json={"choices": [{"finish_reason": "length" if mode == "truncated" else "stop",
                                                   "message": {"content": content}}]})
    with pytest.raises(ProviderError) as error:
        propose_grade(essay_spec, "الماء مهم", consent=True, transport=httpx.MockTransport(handler))
    assert error.value.code == expected


def test_reasoning_models_use_supported_token_parameter_without_temperature(monkeypatch, essay_spec, valid_proposal):
    enable(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "gpt-5-mini")
    def handler(request):
        payload = json.loads(request.content)
        assert "max_completion_tokens" in payload and "max_tokens" not in payload
        assert "temperature" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid_proposal)}}]})
    propose_grade(essay_spec, "الماء مهم", consent=True, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("key,value", [("LLM_MODEL", " "), ("LLM_BASE_URL", "https://:secret@example.org/v1"),
                                      ("LLM_TEMPERATURE", "NaN"), ("LLM_TOKEN_PARAMETER", "invalid")])
def test_invalid_configuration_is_explained_without_a_network_call(monkeypatch, essay_spec, key, value):
    enable(monkeypatch)
    monkeypatch.setenv(key, value)
    assert not all(provider_status().values())
    def forbidden(request):
        pytest.fail("Preflight must not call a paid provider")
    with pytest.raises(ProviderError) as error:
        propose_grade(essay_spec, "الماء", consent=True, transport=httpx.MockTransport(forbidden))
    assert not error.value.attempted and "secret" not in str(error.value)


def test_unassigned_questions_cannot_be_approved_as_blank_answers(monkeypatch):
    enable(monkeypatch)
    spec = ExamSpec(title="متعدد", kind="essay", questions=[
        EssayQuestion(id=f"q{i}", question="سؤال", maximum=2,
                      criteria=[Criterion(id="c", description="فكرة", maximum=2)]) for i in range(2)
    ])
    grade = {"questions": [{"question_id": q.id, "criteria": [
        {"criterion_id": "c", "score": 0, "evidence": "", "rationale": "لم يجب", "missing": ""}
    ]} for q in spec.questions]}
    with pytest.raises(ValueError, match="توزيع"):
        parse_essay_grade(grade, spec, "نص الورقة لم يوزع", {})
    with pytest.raises(ProviderError) as error:
        propose_grade(spec, "نص الورقة", consent=True)
    assert error.value.code == "answers_unsaved" and not error.value.attempted
    assert parse_essay_grade(grade, spec, "", {"essay_answers": {"q0": "", "q1": ""}})[1] == 0


def test_diagnosis_does_not_export_student_text_or_configuration(monkeypatch, essay_spec):
    enable(monkeypatch)
    private = "PRIVATE-STUDENT-ANSWER"
    row = SimpleNamespace(text=private, current={}, original={}, revision=2,
                          proposal=None, approved=None, total=None)
    report = essay_diagnostics(essay_spec, row, organization_allowed=False, visible_text="UNSAVED-DRAFT")
    encoded = json.dumps(report, ensure_ascii=False)
    assert private not in encoded and "UNSAVED-DRAFT" not in encoded and "SYNTHETIC-SECRET" not in encoded
    assert {b["code"] for b in report["blockers"]} >= {"source_unsaved", "organization_ai_disabled"}
    assert report["external_call_performed_by_this_check"] is False


PASSWORD = "Synthetic-Diagnostics!2026"


@pytest.fixture
def service(tmp_path):
    app = create_app(tmp_path / "service")
    auth = app.state.auth
    auth.provision_organization("diag-school", "اصطناعي", "admin@diag.test", "مدير", PASSWORD)
    token = auth.login("diag-school", "admin@diag.test", PASSWORD)
    auth.set_ai_policy(token, True)
    space = Workspace(auth, token)
    with TestClient(app) as client:
        yield app, client, space, token


def test_api_error_retains_grade_returns_current_revision_and_keeps_user_session(
    service, monkeypatch, essay_spec, valid_proposal
):
    app, client, space, token = service
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    space.save_text(sid, "الماء مهم", "", 1)
    space.approve_essay(sid, valid_proposal, "", "", space.submission(sid).revision, True)
    before = space.submission(sid)
    def failed(*args, **kwargs):
        raise ProviderError("provider_auth")
    monkeypatch.setattr("api.propose_grade", failed)
    headers = {"Authorization": "Bearer " + token}
    response = client.post(f"/submissions/{sid}/proposal", headers=headers,
        json={"revision": before.revision, "consent_to_external_processing_and_cost": True})
    assert response.status_code == 502 and response.json()["code"] == "provider_auth"
    after = space.submission(sid)
    assert after.total == 3 and after.approved == before.approved and after.status == "approved"
    assert response.json()["revision"] == after.revision
    assert client.get("/auth/me", headers=headers).status_code == 200
    report = client.get(f"/submissions/{sid}/diagnostics", headers=headers)
    assert report.status_code == 200 and "الماء مهم" not in report.text
    assert report.json()["grade"]["source"] == "teacher_approved"
    assert report.json()["last_attempts"]["llm"]["code"] == "provider_auth"


def test_preflight_failure_does_not_change_submission_revision(service, monkeypatch, essay_spec):
    enable(monkeypatch)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app, client, space, token = service
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    space.save_text(sid, "الماء مهم", "", 1)
    before = space.submission(sid)
    response = client.post(f"/submissions/{sid}/proposal", headers={"Authorization": "Bearer " + token},
                           json={"revision": before.revision, "consent_to_external_processing_and_cost": True})
    assert response.status_code == 412 and response.json()["code"] == "provider_key_missing"
    assert space.submission(sid).revision == before.revision


def test_diagnostics_route_enforces_user_role_and_tenant(service, essay_spec):
    app, client, space, token = service
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    assert client.get(f"/submissions/{sid}/diagnostics").status_code == 401
    app.state.auth.provision_organization("other-diag", "أخرى", "admin@other.test", "مدير", PASSWORD)
    other = app.state.auth.login("other-diag", "admin@other.test", PASSWORD)
    assert client.get(f"/submissions/{sid}/diagnostics", headers={"Authorization": "Bearer " + other}).status_code == 400
    app.state.auth.create_user(token, "viewer@diag.test", "مطلع", PASSWORD, "viewer")
    viewer = app.state.auth.login("diag-school", "viewer@diag.test", PASSWORD)
    app.state.auth.change_password(viewer, PASSWORD, PASSWORD + "-new")
    viewer = app.state.auth.login("diag-school", "viewer@diag.test", PASSWORD + "-new")
    assert client.get(f"/submissions/{sid}/diagnostics", headers={"Authorization": "Bearer " + viewer}).status_code == 403


def test_real_worker_preserves_ocr_failure_code_and_approved_grade(
    service, monkeypatch, essay_spec, valid_proposal
):
    app, client, space, token = service
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    space.save_text(sid, "الماء مهم", "", 1)
    space.approve_essay(sid, valid_proposal, "", "", space.submission(sid).revision, True)
    before = space.submission(sid)
    job = Jobs(space).create("ocr", {"sid": sid, "revision": before.revision})
    monkeypatch.setenv("OCR_PROVIDER", "disabled")
    assert cycle(app.state.auth.root, run_children=True) == 1
    result = Jobs(space).get(job)
    assert result.state == "failed" and result.error == "ocr_disabled"
    after = space.submission(sid)
    assert after.total == 3 and after.approved == before.approved
