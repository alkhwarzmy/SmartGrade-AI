"""Regression, concurrency and injected-failure acceptance for the November assessment."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import subprocess
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from api import create_app
from database.models import Outbox, PendingDeletion, Job, Submission
from modules.auth import AuthService, AuthError, LoginRateLimited, PermissionDenied
from modules.workspace import Workspace, tenant_write_lock
from modules.review import Store, RevisionConflict
from modules.schemas import ExamSpec
from modules.preprocessing import png_bytes
from modules.mcq_grader import template
from modules.outbox import flush_outbox
from modules.jobs import Jobs
from modules.full_backup import backup_full, restore_full, create_key
from scripts.worker import cycle

PASSWORD = "Synthetic-Reliability-Pass!2026"


@pytest.fixture
def setup(tmp_path):
    auth = AuthService(tmp_path / "installation")
    auth.provision_organization("school-one", "SYNTHETIC One", "admin@one.test", "Admin", PASSWORD)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    return auth, token, Workspace(auth, token)


def mcq(space):
    eid = space.create_exam(ExamSpec(title="SYNTHETIC", kind="mcq", answer_key=["A"] * 20))
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    return eid, sid


def count(store, model):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_login_shared_client_cannot_lock_another_organization(setup):
    auth, _, _ = setup
    auth.provision_organization("school-two", "Two", "admin@two.test", "Admin", PASSWORD)
    for i in range(30):
        with pytest.raises(AuthError):
            auth.login("school-one", f"unknown{i}@example.test", "wrong", client_id="same-proxy")
    valid = auth.login("school-two", "admin@two.test", PASSWORD, client_id="same-proxy")
    assert auth.actor(valid).organization_name == "Two"
    for _ in range(5):
        with pytest.raises(AuthError):
            auth.login("school-two", "admin@two.test", "wrong", client_id="one")
    with pytest.raises(LoginRateLimited):
        auth.login("school-two", "admin@two.test", PASSWORD, client_id="another")


def test_proxy_headers_only_from_configured_peer(monkeypatch):
    from modules.proxy import client_address

    monkeypatch.setenv("SMARTGRADE_TRUSTED_PROXY_CIDRS", "172.31.77.10/32")

    def request(peer):
        return SimpleNamespace(
            client=SimpleNamespace(host=peer),
            headers={"x-smartgrade-client-ip": "203.0.113.12", "x-forwarded-for": "198.51.100.1"},
        )

    assert client_address(request("192.0.2.8")) == "192.0.2.8"
    assert client_address(request("172.31.77.10")) == "203.0.113.12"


def test_outbox_survives_audit_failure_and_replay_is_deduplicated(setup):
    auth, token, space = setup
    spec = ExamSpec(title="Atomic", kind="mcq", answer_key=["A"] * 20)
    original = auth.audit

    def committed_then_connection_lost(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError("synthetic lost acknowledgement")

    with patch.object(auth, "audit", committed_then_connection_lost):
        eid = space.create_exam(spec)
    assert space.exam(eid).title == "Atomic"
    assert count(space._core, Outbox) == 1
    with tenant_write_lock(space._root):
        flush_outbox(space._core, auth)
        flush_outbox(space._core, auth)
    events = [e for e in auth.audit_events(token) if e["action"] == "exam.created"]
    assert len(events) == 1 and events[0]["object_id"] == eid
    assert count(space._core, Outbox) == 0 and auth.verify_audit(token)


def test_outbox_insert_failure_rolls_back_data(setup, monkeypatch):
    _, _, space = setup

    def unavailable(*args):
        raise sqlite3.OperationalError("synthetic tenant write failure")

    monkeypatch.setattr(space._core, "_event", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        space.create_exam(ExamSpec(title="Rollback", kind="mcq", answer_key=["A"] * 20))
    assert space.exams() == []


def test_twenty_concurrent_retries_create_one_paper_and_receipt_is_durable(setup):
    auth, token, space = setup
    eid = space.create_exam(ExamSpec(title="Replay", kind="mcq", answer_key=["A"] * 20))
    raw = png_bytes(template())
    with ThreadPoolExecutor(max_workers=20) as pool:
        ids = list(
            pool.map(
                lambda _: Workspace(auth, token).upload(eid, "SYNTHETIC", raw, idempotency_key="retry-1"),
                range(20),
            )
        )
    assert len(set(ids)) == 1 and len(space.submissions()) == 1
    reopened = Workspace(AuthService(auth.root), token)
    assert reopened.upload(eid, "SYNTHETIC", raw, idempotency_key="retry-1") == ids[0]
    with pytest.raises(RevisionConflict):
        space.upload(eid, "DIFFERENT", raw, idempotency_key="retry-1")
    assert space.upload(eid, "SYNTHETIC", raw, idempotency_key="intentional-retake") != ids[0]


def test_failed_delete_is_hidden_referenced_retried_and_blocks_edits(setup):
    auth, token, space = setup
    _, sid = mcq(space)
    row = space.submission(sid)
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path.parent == space._core.images:
            raise PermissionError("synthetic permission failure")
        return original(path, *args, **kwargs)

    with patch.object(Path, "unlink", denied):
        assert space.delete_submission(sid, row.revision) is False
    assert count(space._core, PendingDeletion) == 1
    assert space._core.image_path(row.image_name).exists()
    assert not space.submissions()
    with pytest.raises(RevisionConflict):
        space.approve_mcq(sid, ["A"] * 20, "", "", row.revision, True)
    with tenant_write_lock(space._root):
        assert space._core.retry_deletions() == 1
        flush_outbox(space._core, auth)
    assert count(space._core, PendingDeletion) == 0 and count(space._core, Submission) == 0
    assert not list(space._core.images.iterdir())
    assert any(e["action"] == "submission.deleted" for e in auth.audit_events(token))


def test_unchanged_text_preserves_grade_revision_and_history(setup, essay_spec, valid_proposal):
    _, _, space = setup
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    space.save_text(sid, "الماء ضروري للحياة", "", 1)
    space.approve_essay(sid, valid_proposal, "", "", 2, True)
    approved = space.submission(sid)
    history = len(space.history(sid))
    assert space.save_text(sid, approved.text, "", approved.revision) is False
    saved = space.submission(sid)
    assert (saved.status, saved.total, saved.revision) == ("approved", 3, approved.revision)
    assert len(space.history(sid)) == history
    space.save_text(sid, approved.text + "!", "", approved.revision)
    assert space.submission(sid).status == "review" and space.submission(sid).total is None


def test_api_stale_proposal_is_409_and_upload_replay(setup, essay_spec, monkeypatch):
    auth, token, space = setup
    app = create_app(auth.root)
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "same"}
    eid = space.create_exam(essay_spec)
    with TestClient(app) as client:
        kwargs = dict(
            data={"student_code": "SYNTHETIC"},
            files={"file": ("a.png", png_bytes(template()), "image/png")},
            headers=headers,
        )
        first = client.post(f"/exams/{eid}/submissions", **kwargs)
        second = client.post(f"/exams/{eid}/submissions", **kwargs)
        assert first.status_code == second.status_code == 201 and first.json() == second.json()
        sid = first.json()["id"]
        space.save_text(sid, "الماء", "", 1)
        auth.set_ai_policy(token, True)

        def forbidden(*args, **kwargs):
            pytest.fail("Provider must not run on a stale revision")

        monkeypatch.setattr("api.propose_grade", forbidden)
        response = client.post(
            f"/submissions/{sid}/proposal",
            json={"revision": 1, "consent_to_external_processing_and_cost": True},
            headers=headers,
        )
        assert response.status_code == 409
        assert len(response.headers["x-request-id"]) == 32
        assert client.get("/exams?limit=201", headers=headers).status_code == 422


def test_complete_encrypted_restore_grades_identity_images_and_revokes_sessions(setup, tmp_path):
    auth, token, space = setup
    eid, sid = mcq(space)
    space.approve_mcq(sid, ["A"] * 20, "", "", 1, True)
    Jobs(space).create("report", {"exam_id": eid, "format": "csv"})
    key = create_key(tmp_path / "key")
    archive = tmp_path / "installation.sgb"
    with pytest.raises(ValueError):
        backup_full(auth.root, archive, key)
    backup_full(auth.root, archive, key, services_stopped=True)
    assert archive.read_bytes()[:8] == b"SGFULL2\n"
    restored = tmp_path / "restored"
    restore_full(archive, restored, key)
    recovered = AuthService(restored)
    with pytest.raises(AuthError):
        recovered.actor(token)
    fresh = recovered.login("school-one", "admin@one.test", PASSWORD)
    reopened = Workspace(recovered, fresh)
    assert reopened.submission(sid).total == 20
    assert reopened.image_path(reopened.submission(sid).image_name).is_file()
    assert recovered.verify_audit(fresh)
    with pytest.raises(ValueError):
        restore_full(archive, restored, key)
    damaged = tmp_path / "damaged.sgb"
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 1
    damaged.write_bytes(raw)
    with pytest.raises(ValueError, match="توثيق"):
        restore_full(damaged, tmp_path / "must-not-exist", key)
    assert not (tmp_path / "must-not-exist").exists()
    wrong = create_key(tmp_path / "wrong-key")
    with pytest.raises(ValueError, match="توثيق"):
        restore_full(archive, tmp_path / "wrong-restore", wrong)
    assert not (tmp_path / "wrong-restore").exists()


def test_jobs_report_ocr_cancel_retry_stale_and_cross_tenant(setup, essay_spec, monkeypatch):
    auth, token, space = setup
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    jobs = Jobs(space)
    jid = jobs.create("ocr", {"sid": sid, "revision": 1})
    monkeypatch.setattr("modules.handwriting_ocr.recognize", lambda raw: "الماء")
    assert cycle(auth.root, run_children=False) == 1
    assert jobs.get(jid).state == "succeeded" and space.submission(sid).text == "الماء"
    report = jobs.create("report", {"exam_id": eid, "format": "csv"})
    cycle(auth.root, run_children=False)
    assert jobs.get(report).state == "succeeded" and jobs.result_path(report).read_bytes()
    cancelled = jobs.create("report", {"exam_id": eid, "format": "xlsx"})
    jobs.change(cancelled, "cancel")
    assert cycle(auth.root, run_children=False) == 0
    jobs.change(cancelled, "retry")
    cycle(auth.root, run_children=False)
    assert jobs.get(cancelled).state == "succeeded"
    old = jobs.create("ocr", {"sid": sid, "revision": space.submission(sid).revision})
    space.save_text(sid, "الماء مهم", "", space.submission(sid).revision)
    cycle(auth.root, run_children=False)
    assert jobs.get(old).state == "failed" and space.submission(sid).text == "الماء مهم"
    auth.provision_organization("school-two", "Two", "other@two.test", "Other", PASSWORD)
    other = Workspace(auth, auth.login("school-two", "other@two.test", PASSWORD))
    with pytest.raises(PermissionDenied):
        Jobs(other).get(jid)


def test_worker_timeout_and_restart_are_retryable(setup, monkeypatch):
    auth, _, space = setup
    eid, _ = mcq(space)
    jobs = Jobs(space)
    jid = jobs.create("report", {"exam_id": eid, "format": "csv"})

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic child", 120)

    monkeypatch.setattr("scripts.worker.subprocess.run", timeout)
    cycle(auth.root)
    assert jobs.get(jid).state == "failed" and jobs.get(jid).error == "time_limit_120s"
    jobs.change(jid, "retry")
    with space._core.sessions.begin() as db:
        db.get(Job, jid).state = "running"
    cycle(auth.root, run_children=False)
    assert jobs.get(jid).state == "failed"
    jobs.change(jid, "retry")
    cycle(auth.root, run_children=False)
    assert jobs.get(jid).state == "succeeded"


def test_operator_recovery_existing_admin_only(setup):
    auth, token, _ = setup
    auth.recover_admin("school-one", "admin@one.test", "Temporary-New-Pass!2027")
    with pytest.raises(AuthError):
        auth.actor(token)
    fresh = auth.login("school-one", "admin@one.test", "Temporary-New-Pass!2027")
    assert auth.actor(fresh, allow_password_change=True).must_change_password
    with pytest.raises(PermissionDenied):
        auth.actor(fresh)
    with pytest.raises(ValueError):
        auth.recover_admin("school-one", "missing@one.test", PASSWORD)


def test_additive_migration_preserves_existing_rows_and_refuses_future_schema(setup):
    _, _, space = setup
    eid, sid = mcq(space)
    with space._core.engine.begin() as db:
        db.exec_driver_sql("PRAGMA user_version=1")
    reopened = Store(space._root)
    assert reopened.exam(eid) and reopened.submission(sid)
    with reopened.engine.begin() as db:
        db.exec_driver_sql("PRAGMA user_version=99")
    with pytest.raises(RuntimeError):
        Store(space._root)


def test_crop_rotation_is_explicit_and_bad_crops_rejected():
    import numpy as np
    from modules.preprocessing import prepare_image, decode_image

    image = np.full((200, 300, 3), 255, dtype=np.uint8)
    image[:100, :100] = 0
    raw = png_bytes(image)
    assert decode_image(prepare_image(raw, 90)).shape == (300, 200, 3)
    assert decode_image(prepare_image(raw, 7)).shape[0] > 200
    assert decode_image(prepare_image(raw, 0, (0, 50), (0, 100))).shape == (200, 150, 3)
    with pytest.raises(ValueError):
        prepare_image(raw, 0, (99, 100), (0, 100))


def test_chunked_body_rolls_to_disk_and_downstream_receives_small_chunks():
    import asyncio
    from modules.http_limits import RequestSizeLimit

    messages = []
    sizes = []

    async def inner(scope, receive, send):
        total = 0
        while True:
            msg = await receive()
            sizes.append(len(msg["body"]))
            total += len(msg["body"])
            if not msg.get("more_body"):
                break
        assert total == 400_000
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * 200_000, "more_body": True},
            {"type": "http.request", "body": b"y" * 200_000, "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    async def send(message):
        messages.append(message)

    asyncio.run(RequestSizeLimit(inner)({"type": "http", "headers": []}, receive, send))
    assert len(sizes) > 1 and max(sizes) <= 65536
    assert messages[0]["status"] == 200


def test_orphan_reconciliation_never_deletes_referenced_or_recent_images(setup):
    import os
    import time
    from modules.operations import reconcile_files

    _, _, space = setup
    _, sid = mcq(space)
    referenced = space._core.image_path(space.submission(sid).image_name)
    old = space._core.images / ("0" * 32 + ".png")
    recent = space._core.images / ("1" * 32 + ".png")
    old.write_bytes(b"orphan")
    recent.write_bytes(b"recent")
    for path in (old, referenced):
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    with tenant_write_lock(space._root):
        assert reconcile_files(space._core)["orphan_candidates"] == 1
        assert old.exists()
        assert reconcile_files(space._core, execute=True)["deleted"] == 1
    assert referenced.exists() and recent.exists() and not old.exists()


def test_job_refuses_permission_revoked_during_ocr(setup, essay_spec, monkeypatch):
    auth, _, space = setup
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    jid = Jobs(space).create("ocr", {"sid": sid, "revision": 1})

    def suspended_during_work(raw):
        auth.set_license("school-one", "pilot", active=False)
        return "must not be saved"

    monkeypatch.setattr("modules.handwriting_ocr.recognize", suspended_during_work)
    cycle(auth.root, run_children=False)
    assert space._core.submission(sid).text == ""
    with space._core.sessions() as db:
        assert db.get(Job, jid).state == "failed"


def test_readiness_fails_closed_on_low_disk(setup, monkeypatch):
    auth, _, _ = setup
    monkeypatch.setattr("modules.operations.shutil.disk_usage", lambda root: SimpleNamespace(free=0))
    with TestClient(create_app(auth.root)) as client:
        assert client.get("/ready").status_code == 503


def test_reference_dataset_detects_split_leak_and_never_certifies_toy_data():
    from scripts.evaluate_dataset import evaluate

    data = {
        "authorized": True,
        "source_kind": "synthetic",
        "samples": [
            {
                "id": "one",
                "writer_group": "w1",
                "source_group": "a",
                "split": "train",
                "kind": "ocr",
                "reference": "الماء",
                "prediction": "الماء",
            },
            {
                "id": "two",
                "writer_group": "w2",
                "source_group": "b",
                "split": "test",
                "kind": "ocr",
                "reference": "المَاء",
                "prediction": "الماء",
            },
        ],
    }
    report = evaluate(data)
    assert report["metrics"]["ocr"]["normalized"]["CER"] == 0
    assert report["metrics"]["ocr"]["raw"]["CER"] > 0
    assert report["accuracy_certified"] is False
    data["samples"][1]["writer_group"] = "w1"
    with pytest.raises(ValueError, match="تسرب"):
        evaluate(data)


def test_api_reports_deletion_pending_instead_of_false_completion(setup):
    auth, token, space = setup
    _, sid = mcq(space)
    original = Path.unlink

    def deny_image(path, *args, **kwargs):
        if path.parent == space._core.images:
            raise PermissionError("synthetic unlink failure")
        return original(path, *args, **kwargs)

    with TestClient(create_app(auth.root)) as client, patch.object(Path, "unlink", deny_image):
        response = client.delete(
            f"/submissions/{sid}?revision=1", headers={"Authorization": "Bearer " + token}
        )
    assert response.status_code == 202 and response.json()["status"] == "deleting"
    assert count(space._core, PendingDeletion) == 1


def test_cached_report_expires_on_real_edit_and_cleanup_retries(setup, essay_spec, valid_proposal):
    from scripts.worker import sweep

    auth, _, space = setup
    eid = space.create_exam(essay_spec)
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    space.save_text(sid, "الماء ضروري للحياة", "", 1)
    space.approve_essay(sid, valid_proposal, "", "", 2, True)
    jobs = Jobs(space)
    jid = jobs.create("report", {"exam_id": eid, "format": "csv"})
    cycle(auth.root, run_children=False)
    path = jobs.result_path(jid)
    current = space.submission(sid)
    space.save_text(sid, current.text, "", current.revision)
    assert jobs.get(jid).state == "succeeded"
    space.save_text(sid, current.text + "!", "", current.revision)
    assert jobs.get(jid).state == "expired"
    with pytest.raises(ValueError):
        jobs.result_path(jid)
    with tenant_write_lock(space._root):
        sweep(space._core)
    assert not path.exists()


def test_body_deadline_is_total_and_cannot_be_extended_by_small_chunks():
    import asyncio
    from modules.http_limits import RequestSizeLimit

    sent = []

    async def never_called(scope, receive, send):
        pytest.fail("Body deadline must reject before endpoint execution")

    async def receive():
        await asyncio.sleep(0.03)
        return {"type": "http.request", "body": b"x", "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(
        RequestSizeLimit(never_called, body_seconds=0.05)({"type": "http", "headers": []}, receive, send)
    )
    assert sent[0]["status"] == 408


def test_incomplete_backup_is_never_published(setup, tmp_path, monkeypatch):
    auth, _, space = setup
    mcq(space)
    key = create_key(tmp_path / "key")
    destination = tmp_path / "snapshot.sgb"

    def fail_mid_encryption(source, target, key):
        target.write(b"incomplete encrypted artifact")
        raise OSError("synthetic output failure")

    monkeypatch.setattr("modules.full_backup._encrypt", fail_mid_encryption)
    with pytest.raises(OSError):
        backup_full(auth.root, destination, key, services_stopped=True)
    assert not destination.exists() and len(space.submissions()) == 1
