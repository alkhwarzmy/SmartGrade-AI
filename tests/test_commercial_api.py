import pytest
from fastapi.testclient import TestClient
from api import create_app
from modules.schemas import ExamSpec
from modules.mcq_grader import template
from modules.preprocessing import png_bytes

PASSWORD = "API-synthetic-password!2026"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "service")
    app.state.auth.provision_organization(
        "academy-one", "أكاديمية أولى", "admin@one.test", "المدير", PASSWORD
    )
    with TestClient(app) as client:
        yield client


def login(client, organization="academy-one", email="admin@one.test", password=PASSWORD):
    response = client.post(
        "/auth/login", json={"organization": organization, "email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_health_and_anonymous_access_denied(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    for route in ["/exams", "/admin/users", "/organization/usage", "/admin/audit"]:
        response = client.get(route)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404


def test_validation_does_not_echo_password(client):
    marker = "DO-NOT-ECHO-THIS-SECRET-" * 20
    response = client.post(
        "/auth/login", json={"organization": "academy-one", "email": "admin@one.test", "password": marker}
    )
    assert response.status_code == 422 and marker not in response.text


def test_api_full_mcq_workflow_and_pdf(client):
    headers = login(client)
    response = client.post(
        "/exams", json=ExamSpec(title="علوم", kind="mcq", answer_key=["A"] * 20).model_dump(), headers=headers
    )
    assert response.status_code == 201, response.text
    eid = response.json()["id"]
    image = png_bytes(template({i: ["A"] for i in range(20)}, True))
    response = client.post(
        f"/exams/{eid}/submissions",
        data={"student_code": "SYNTHETIC-API"},
        files={"file": ("../../malicious.jpg", image, "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    row = client.get(f"/submissions/{sid}", headers=headers).json()
    assert row["status"] == "review" and row["total"] is None
    assert client.get(f"/submissions/{sid}/image", headers=headers).headers["content-type"] == "image/png"
    body = {"answers": ["A"] * 20, "revision": row["revision"], "reviewed": True, "reason": "مراجعة اصطناعية"}
    response = client.post(f"/submissions/{sid}/approve-mcq", json=body, headers=headers)
    assert response.status_code == 200 and response.json()["total"] == 20
    for kind in ["xlsx", "csv", "pdf"]:
        response = client.get(f"/exams/{eid}/report.{kind}", headers=headers)
        assert response.status_code == 200 and len(response.content) > 100
        if kind == "pdf":
            import fitz

            document = fitz.open(stream=response.content, filetype="pdf")
            assert document.page_count >= 1
    response = client.post(f"/submissions/{sid}/approve-mcq", json=body, headers=headers)
    assert response.status_code in {400, 409}  # Stale revision is not silently accepted.
    response = client.get("/admin/audit", headers=headers)
    assert response.json()["chain_valid"]
    assert any(e["action"] == "submission.mcq_approved" for e in response.json()["events"])


def test_api_cross_tenant_and_viewer_authorization(client):
    owner = login(client)
    eid = client.post(
        "/exams", json=ExamSpec(title="خاص", kind="mcq", answer_key=["A"] * 20).model_dump(), headers=owner
    ).json()["id"]
    client.app.state.auth.provision_organization("academy-two", "ثانية", "admin@two.test", "مدير", PASSWORD)
    other = login(client, "academy-two", "admin@two.test")
    assert client.get("/exams", headers=other).json() == []
    response = client.get(f"/exams/{eid}/submissions", headers=other)
    assert response.status_code == 400
    assert client.get(f"/exams/{eid}/report.xlsx", headers=other).status_code == 400
    uid = client.post(
        "/admin/users",
        json={"email": "viewer@one.test", "name": "مشاهد", "temporary_password": PASSWORD, "role": "viewer"},
        headers=owner,
    ).json()["id"]
    viewer = login(client, email="viewer@one.test")
    assert client.get("/exams", headers=viewer).status_code == 403  # Forced first password change.
    new = "New-API-viewer-password!2027"
    assert (
        client.post(
            "/auth/change-password", json={"current_password": PASSWORD, "new_password": new}, headers=viewer
        ).status_code
        == 200
    )
    viewer = login(client, email="viewer@one.test", password=new)
    assert client.get("/admin/users", headers=viewer).status_code == 403
    assert (
        client.post(
            "/exams", json=ExamSpec(title="x", kind="mcq", answer_key=["A"] * 20).model_dump(), headers=viewer
        ).status_code
        == 403
    )
    result = client.get("/exams", headers=viewer)
    assert result.status_code == 200 and "spec" not in result.json()[0]
    client.patch(f"/admin/users/{uid}", json={"role": "viewer", "active": False}, headers=owner)
    assert client.get("/exams", headers=viewer).status_code == 401


def test_api_external_ai_requires_institution_permission(client, essay_spec):
    headers = login(client)
    eid = client.post("/exams", json=essay_spec.model_dump(), headers=headers).json()["id"]
    sid = client.post(
        f"/exams/{eid}/submissions",
        data={"student_code": "SYNTHETIC"},
        files={"file": ("answer.png", png_bytes(template()), "image/png")},
        headers=headers,
    ).json()["id"]
    response = client.post(
        f"/submissions/{sid}/proposal",
        json={"revision": 1, "consent_to_external_processing_and_cost": True},
        headers=headers,
    )
    assert response.status_code == 403
    assert client.get(f"/submissions/{sid}", headers=headers).json()["total"] is None


def test_api_oversized_request_rejected_even_without_content_length(client):
    payload = b"x" * (11 * 1024 * 1024 + 1)
    response = client.post("/auth/login", content=payload)
    assert response.status_code == 413


def test_logout_revokes_token(client):
    headers = login(client)
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/exams", headers=headers).status_code == 401


def test_login_rate_limit_returns_retry_after(client):
    for _ in range(5):
        assert (
            client.post(
                "/auth/login",
                json={"organization": "academy-one", "email": "admin@one.test", "password": "wrong"},
            ).status_code
            == 401
        )
    response = client.post(
        "/auth/login", json={"organization": "academy-one", "email": "admin@one.test", "password": PASSWORD}
    )
    assert response.status_code == 429 and response.headers["Retry-After"] == "900"


def test_ocr_api_requires_current_revision_and_records_failure(client, essay_spec, monkeypatch):
    headers = login(client)
    eid = client.post("/exams", json=essay_spec.model_dump(), headers=headers).json()["id"]
    sid = client.post(
        f"/exams/{eid}/submissions",
        data={"student_code": "SYNTHETIC-OCR"},
        files={"file": ("answer.png", png_bytes(template()), "image/png")},
        headers=headers,
    ).json()["id"]
    monkeypatch.setattr("api.recognize", lambda raw: "الماء ضروري للحياة")
    response = client.post(f"/submissions/{sid}/ocr", json={"revision": 1}, headers=headers)
    assert response.status_code == 200 and response.json()["text"] == "الماء ضروري للحياة"
    assert client.post(f"/submissions/{sid}/ocr", json={"revision": 1}, headers=headers).status_code == 409

    def unavailable(raw):
        raise ValueError("تعذر استخراج نص صالح")

    monkeypatch.setattr("api.recognize", unavailable)
    response = client.post(
        f"/submissions/{sid}/ocr", json={"revision": response.json()["revision"]}, headers=headers
    )
    assert response.status_code == 400
    row = client.get(f"/submissions/{sid}", headers=headers).json()
    assert row["total"] is None and row["original"]["ocr_failed"]


def test_mobile_and_multi_essay_endpoints(client):
    headers = login(client)
    spec = {"title": "متعدد", "kind": "essay", "questions": [
        {"id": f"q{i}", "question": f"سؤال {i}", "maximum": 4,
         "criteria": [{"id": "c1", "description": "الفكرة", "maximum": 4}]} for i in range(1, 3)
    ]}
    response = client.post("/exams", json=spec, headers=headers)
    assert response.status_code == 201, response.text
    eid = response.json()["id"]
    summary = client.get("/exams?include_spec=false", headers=headers).json()[0]
    assert "spec" not in summary and summary["question_count"] == 2 and summary["maximum"] == 8
    assert len(client.get(f"/exams/{eid}", headers=headers).json()["spec"]["questions"]) == 2
    sid = client.post(f"/exams/{eid}/submissions", headers=headers, data={"student_code": "SYNTHETIC"},
                     files={"file": ("paper.png", png_bytes(template()), "image/png")}).json()["id"]
    answers = {"q1": "الماء", "q2": "الشمس"}
    saved = client.put(f"/submissions/{sid}/essay-answers", headers=headers,
                       json={"revision": 1, "answers": answers})
    assert saved.status_code == 200
    grade = {"questions": [{"question_id": q, "criteria": [{"criterion_id": "c1", "score": 3,
              "evidence": a, "rationale": "شرح جزئي", "missing": ""}]} for q, a in answers.items()]}
    result = client.post(f"/submissions/{sid}/approve-essay", headers=headers,
                         json={"revision": saved.json()["revision"], "reviewed": True, "proposal": grade})
    assert result.status_code == 200 and result.json()["total"] == 6
    assert client.put(f"/submissions/{sid}/essay-answers", headers=headers,
                      json={"revision": 1, "answers": answers}).status_code == 409
    assert client.get(f"/submissions/{sid}/image?preview_width=200", headers=headers).status_code == 200
    for kind in ["xlsx", "csv", "pdf"]:
        assert client.get(f"/exams/{eid}/report.{kind}", headers=headers).status_code == 200


@pytest.mark.parametrize("count", [1, 7, 50])
def test_api_dynamic_mcq_template_upload_and_approval(client, count):
    headers = login(client)
    created = client.post("/exams", json={"title": "اختبار متغير", "kind": "mcq",
                          "answer_key": ["A"] * count, "points_per_question": 2}, headers=headers)
    assert created.status_code == 201, created.text
    eid = created.json()["id"]
    exam = client.get(f"/exams/{eid}", headers=headers).json()
    assert exam["question_count"] == count and exam["maximum"] == 2 * count
    assert next(e for e in client.get("/exams", headers=headers).json() if e["id"] == eid)["question_count"] == count
    assert client.get(f"/exams/{eid}/omr-template").status_code == 401
    for kind in ["pdf", "png"]:
        response = client.get(f"/exams/{eid}/omr-template?format={kind}", headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        if kind == "png":
            from modules.preprocessing import decode_image
            from modules.mcq_grader import read_answers
            _, rows = read_answers(decode_image(response.content), question_count=count)
            assert len(rows) == count and all(r["state"] == "blank" for r in rows)
        else:
            assert response.content.startswith(b"%PDF-")
    image = png_bytes(template({i: ["A"] for i in range(count)}, True, question_count=count))
    response = client.post(f"/exams/{eid}/submissions", data={"student_code": "SYNTHETIC-DYNAMIC"},
                           files={"file": ("dynamic.png", image, "image/png")}, headers=headers)
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    wrong = ["A"] * (count - 1 if count > 1 else 2)
    response = client.post(f"/submissions/{sid}/approve-mcq",
                          json={"answers": wrong, "revision": 1, "reviewed": True}, headers=headers)
    assert response.status_code in {400, 422}
    assert client.get(f"/submissions/{sid}", headers=headers).json()["total"] is None
    response = client.post(f"/submissions/{sid}/approve-mcq",
                          json={"answers": ["A"] * count, "revision": 1, "reviewed": True}, headers=headers)
    assert response.status_code == 200 and response.json()["total"] == 2 * count
    assert client.get(f"/exams/{eid}/report.xlsx", headers=headers).status_code == 200


@pytest.mark.parametrize("count", [0, 51])
def test_api_rejects_empty_or_over_limit_mcq(client, count):
    headers = login(client)
    response = client.post("/exams", json={"title": "خارج الحد", "kind": "mcq",
                           "answer_key": ["A"] * count}, headers=headers)
    assert response.status_code == 422


def test_mcq_template_is_scoped_to_institution_and_read_permission(client):
    headers = login(client)
    eid = client.post("/exams", json={"title": "خاص", "kind": "mcq", "answer_key": ["A"] * 7},
                      headers=headers).json()["id"]
    client.app.state.auth.provision_organization("academy-two", "ثانية", "admin@two.test", "مدير", PASSWORD)
    other = login(client, "academy-two", "admin@two.test")
    assert client.get(f"/exams/{eid}/omr-template", headers=other).status_code == 400
    client.post("/admin/users", json={"email": "viewer@one.test", "name": "مشاهد",
                "temporary_password": PASSWORD, "role": "viewer"}, headers=headers)
    viewer = login(client, email="viewer@one.test")
    new = "New-viewer-password-2027!"
    client.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": new}, headers=viewer)
    viewer = login(client, email="viewer@one.test", password=new)
    assert client.get(f"/exams/{eid}/omr-template", headers=viewer).status_code == 403
