import hashlib
import sqlite3
import pytest
from modules.auth import (
    AuthService,
    AuthError,
    PermissionDenied,
    CapacityError,
    hash_password,
    verify_password,
    PLAN_LIMITS,
)
from modules.workspace import Workspace
from modules.schemas import ExamSpec
from modules.mcq_grader import template
from modules.preprocessing import png_bytes

PASSWORD = "Synthetic-only-Password!2026"
NEW_PASSWORD = "Different-synthetic-Password!2027"


@pytest.fixture
def identity(tmp_path):
    auth = AuthService(tmp_path / "commercial")
    oid = auth.provision_organization("school-one", "مدرسة أولى", "admin@one.test", "المدير", PASSWORD)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    return auth, oid, token


def test_password_hash_and_no_default_account(tmp_path):
    auth = AuthService(tmp_path)
    assert not auth.has_organizations() and auth.ready()
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first) and not verify_password("wrong", first)
    assert not verify_password(PASSWORD, "corrupt")
    with pytest.raises(ValueError):
        hash_password("short")


def test_sessions_are_hashed_revoked_and_expire(identity):
    auth, _, token = identity
    with auth.connection() as db:
        row = db.execute("SELECT token_hash FROM sessions").fetchone()
        assert row[0] != token and row[0] == hashlib.sha256(token.encode()).hexdigest()
    assert auth.actor(token).role == "admin"
    auth.logout(token)
    with pytest.raises(AuthError):
        auth.actor(token)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    other = auth.login("school-one", "admin@one.test", PASSWORD)
    assert auth.revoke_organization_sessions("school-one") == 2
    with pytest.raises(AuthError):
        auth.actor(other)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    auth.clock = lambda: 4_000_000_000
    with pytest.raises(AuthError):
        auth.actor(token)


def test_idle_timeout(tmp_path):
    clock = [1_800_000_000]
    auth = AuthService(tmp_path, clock=lambda: clock[0], idle_minutes=1)
    auth.provision_organization("school-one", "School", "admin@one.test", "Admin", PASSWORD)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    clock[0] += 61
    with pytest.raises(AuthError):
        auth.actor(token)


def test_rate_limit_persists_and_unknown_accounts_are_generic(identity):
    auth, _, _ = identity
    for _ in range(5):
        with pytest.raises(AuthError, match="تعذر تسجيل الدخول"):
            auth.login("school-one", "admin@one.test", "incorrect", client_id="test-client")
    with pytest.raises(AuthError, match="محاولات كثيرة"):
        auth.login("school-one", "admin@one.test", PASSWORD, client_id="test-client")
    with pytest.raises(AuthError, match="تعذر تسجيل الدخول"):
        auth.login("missing", "none@invalid.test", PASSWORD, client_id="other-client")


def test_temporary_password_change_and_reset_revokes_all_sessions(identity):
    auth, _, admin = identity
    uid = auth.create_user(admin, "teacher@one.test", "معلم", PASSWORD, "teacher")
    token = auth.login("school-one", "teacher@one.test", PASSWORD)
    with pytest.raises(PermissionDenied, match="غيّر"):
        Workspace(auth, token)
    auth.change_password(token, PASSWORD, NEW_PASSWORD)
    with pytest.raises(AuthError):
        auth.actor(token)
    token = auth.login("school-one", "teacher@one.test", NEW_PASSWORD)
    assert auth.actor(token).role == "teacher"
    auth.reset_password(admin, uid, PASSWORD)
    with pytest.raises(AuthError):
        auth.actor(token)
    reset_session = auth.login("school-one", "teacher@one.test", PASSWORD)
    assert auth.actor(reset_session, allow_password_change=True).must_change_password


def test_disable_role_change_and_admin_protection(identity):
    auth, _, admin = identity
    uid = auth.create_user(admin, "teacher@one.test", "معلم", PASSWORD, "teacher")
    teacher = auth.login("school-one", "teacher@one.test", PASSWORD)
    auth.change_password(teacher, PASSWORD, NEW_PASSWORD)
    teacher = auth.login("school-one", "teacher@one.test", NEW_PASSWORD)
    with pytest.raises(PermissionDenied):
        auth.list_users(teacher)
    auth.update_user(admin, uid, role="viewer", active=True)
    with pytest.raises(AuthError):
        auth.actor(teacher)
    with pytest.raises(ValueError):
        auth.update_user(admin, auth.actor(admin).user_id, role="teacher", active=True)
    auth.update_user(admin, uid, role="viewer", active=False)
    with pytest.raises(AuthError):
        auth.login("school-one", "teacher@one.test", NEW_PASSWORD)


def test_seat_limits_and_license_expiration(identity, monkeypatch):
    auth, _, admin = identity
    monkeypatch.setitem(PLAN_LIMITS, "pilot", {"users": 1, "exams": 10, "submissions": 10, "storage_mb": 100})
    with pytest.raises(CapacityError):
        auth.create_user(admin, "extra@one.test", "Extra", PASSWORD, "teacher")
    auth.set_license("school-one", "pilot", renewal_until=1)
    with pytest.raises(AuthError):
        auth.actor(admin)


def test_cross_tenant_ids_images_users_and_audit_are_isolated(identity):
    auth, one_id, token_one = identity
    two_id = auth.provision_organization("school-two", "مدرسة ثانية", "admin@two.test", "مدير ثان", PASSWORD)
    token_two = auth.login("school-two", "admin@two.test", PASSWORD)
    one, two = Workspace(auth, token_one), Workspace(auth, token_two)
    eid = one.create_exam(ExamSpec(title="اختبار خاص", kind="mcq", answer_key=["A"] * 20))
    sid = one.upload(eid, "SYNTHETIC", png_bytes(template({0: ["A"]}, True)))
    row = one.submission(sid)
    assert one_id != two_id and not two.exams() and not two.submissions()
    for operation in [
        lambda: two.exam(eid),
        lambda: two.submission(sid),
        lambda: two.history(sid),
        lambda: two.submissions(eid),
        lambda: two.delete_submission(sid, 1),
        lambda: two.approve_mcq(sid, ["A"] * 20, "spoof", "", 1, True),
    ]:
        with pytest.raises(ValueError):
            operation()
    assert not two.image_path(row.image_name).exists()
    with pytest.raises(ValueError):
        auth.update_user(token_two, auth.actor(token_one).user_id, role="admin", active=False)
    assert all(e["organization_id"] == two_id for e in auth.audit_events(token_two))
    one.approve_mcq(sid, ["A"] * 20, "spoofed reviewer", "", 1, True)
    assert one.history(sid)[-1].reviewer == auth.actor(token_one).user_id
    assert auth.verify_audit(token_one)


def test_viewer_can_only_see_approved_reporting_data(identity):
    auth, _, admin = identity
    auth.create_user(admin, "viewer@one.test", "مشاهد", PASSWORD, "viewer")
    token = auth.login("school-one", "viewer@one.test", PASSWORD)
    auth.change_password(token, PASSWORD, NEW_PASSWORD)
    token = auth.login("school-one", "viewer@one.test", NEW_PASSWORD)
    owner, viewer = Workspace(auth, admin), Workspace(auth, token)
    spec = ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20)
    eid = owner.create_exam(spec)
    sid = owner.upload(eid, "SYNTHETIC", png_bytes(template()))
    assert not viewer.submissions()
    for operation in [
        lambda: viewer.create_exam(spec),
        lambda: viewer.submission(sid),
        lambda: viewer.image_path("a.png"),
        lambda: viewer.delete_submission(sid, 1),
        lambda: viewer.approve_mcq(sid, ["A"] * 20, "", "", 1, True),
    ]:
        with pytest.raises(PermissionDenied):
            operation()
    owner.approve_mcq(sid, ["A"] * 20, "", "", 1, True)
    result = viewer.submissions()[0]
    assert result.total == 20 and not hasattr(result, "image_name") and not hasattr(result, "text")


def test_exam_and_submission_quotas(identity, monkeypatch):
    auth, _, token = identity
    monkeypatch.setitem(PLAN_LIMITS, "pilot", {"users": 5, "exams": 1, "submissions": 1, "storage_mb": 100})
    space = Workspace(auth, token)
    spec = ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20)
    eid = space.create_exam(spec)
    with pytest.raises(CapacityError):
        space.create_exam(spec)
    space.upload(eid, "SYNTHETIC", png_bytes(template()))
    with pytest.raises(CapacityError):
        space.upload(eid, "SYNTHETIC2", png_bytes(template()))
    assert space.usage()["submissions"] == 1


def test_audit_is_append_only_and_scoped(identity):
    auth, _, token = identity
    auth.set_ai_policy(token, True)
    assert auth.organization(token)["external_ai_allowed"]
    assert auth.verify_audit(token)
    with auth.connection(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE audit_events SET action='tampered'")
    actions = [e["action"] for e in auth.audit_events(token)]
    assert "organization.ai_policy_changed" in actions
