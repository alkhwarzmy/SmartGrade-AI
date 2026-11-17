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
