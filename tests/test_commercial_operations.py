from concurrent.futures import ThreadPoolExecutor
import pytest
from modules.auth import AuthService, PLAN_LIMITS, CapacityError
from modules.workspace import Workspace
from modules.mcq_grader import template
from modules.preprocessing import png_bytes
from modules.schemas import ExamSpec
from modules.private_artifacts import download_html, image_html
from scripts.retention import purge

PASSWORD = "Operations-synthetic-Pass!2026"


def setup(root):
    auth = AuthService(root)
    auth.provision_organization("school-one", "مدرسة", "admin@one.test", "مدير", PASSWORD)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    return auth, token, Workspace(auth, token)


def test_quota_is_enforced_under_concurrent_calls(tmp_path, monkeypatch):
    _, _, space = setup(tmp_path)
    monkeypatch.setitem(PLAN_LIMITS, "pilot", {"users": 5, "exams": 1, "submissions": 10, "storage_mb": 10})
    spec = ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20)

    def attempt(_):
        try:
            return space.create_exam(spec)
        except CapacityError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(attempt, range(4)))
    assert len([r for r in results if r]) == 1
    assert len(space.exams()) == 1


def test_storage_quota_refuses_before_creating_submission(tmp_path, monkeypatch):
    _, _, space = setup(tmp_path)
    eid = space.create_exam(ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20))
    monkeypatch.setitem(
        PLAN_LIMITS, "pilot", {"users": 5, "exams": 10, "submissions": 10, "storage_mb": 0.001}
    )
    with pytest.raises(ValueError, match="مساحة"):
        space.upload(eid, "SYNTHETIC", png_bytes(template()))
    assert not space.submissions()
    assert not list(space._core.images.iterdir())


def test_retention_dry_run_and_execute_are_scoped(tmp_path):
    auth, token, space = setup(tmp_path)
    eid = space.create_exam(ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20))
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    path = space.image_path(space.submission(sid).image_name)
    with space._core.sessions.begin() as db:
        from database.models import Submission

        db.get(Submission, sid).created_at = "2000-01-01T00:00:00+00:00"
    report = purge(tmp_path, "school-one", 365)
    assert report == {"candidates": 1, "deleted": 0, "dry_run": True}
    assert path.exists()
    report = purge(tmp_path, "school-one", 365, execute=True)
    assert report["deleted"] == 1 and not path.exists() and not space.submissions()
    assert auth.verify_audit(token)
    assert any(e["action"] == "retention.deleted" for e in auth.audit_events(token))


def test_private_download_markup_is_escaped_and_has_no_public_url():
    html = download_html('<img onerror="x">', b"a,b\n1,2", 'report".csv', "text/csv")
    assert 'href="data:text/csv;base64,' in html
    assert "&lt;img" in html and 'download="report&quot;.csv"' in html
    assert "https://" not in html and "/media/" not in html
    with pytest.raises(ValueError):
        download_html("bad", b"x", "a.html", "text/html")
    html = image_html(template())
    assert 'src="data:image/png;base64,' in html and "/media/" not in html


def test_contract_calendar_date_expires_at_riyadh_end_of_day():
    from scripts.manage import _expiry
    from datetime import datetime, timezone

    assert (
        datetime.fromtimestamp(_expiry("2027-12-31"), timezone.utc).isoformat() == "2027-12-31T20:59:59+00:00"
    )
