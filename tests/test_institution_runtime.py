"""Real institution startup and recovery with isolated synthetic credentials, never notebook defaults."""

from pathlib import Path
import ast
import socket
import subprocess
import sys
import time
import pytest
from modules.auth import AuthService, AuthError
from modules.institution_runtime import launch_institution, InstitutionRuntime, _start_time
from modules.colab_runtime import AppRuntime, launch_app
from modules.workspace import Workspace
from modules.jobs import Jobs
from modules.schemas import ExamSpec
from modules.preprocessing import png_bytes
from modules.mcq_grader import template
from modules.full_backup import create_key, backup_full, restore_full
from scripts.build_institution_notebook import build_institution_notebook

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "SYNTHETIC-Institution-Runtime!2026"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_commercial_launcher_requires_operator_account_and_never_seeds_defaults(tmp_path):
    with pytest.raises(ValueError, match="حساب المدير"):
        launch_institution(ROOT, sys.executable, tmp_path / "empty", port=free_port())
    assert not AuthService(tmp_path / "empty").has_organizations()


def test_invalid_mode_and_demo_override_rejected(tmp_path):
    with pytest.raises(ValueError, match="وضع التشغيل"):
        launch_app(ROOT, sys.executable, tmp_path / "bad", mode="unknown")
    with pytest.raises(ValueError, match="غير مسموح"):
        launch_app(
            ROOT,
            sys.executable,
            tmp_path / "bad",
            mode="commercial",
            port=free_port(),
            extra_env={"SMARTGRADE_MODE": "demo"},
        )


def test_authenticated_web_worker_restart_and_encrypted_recovery(tmp_path, monkeypatch):
    auth = AuthService(tmp_path / "data")
    auth.provision_organization(
        "my-school", "SYNTHETIC Institution", "admin@institution.test", "Admin", PASSWORD, plan="school"
    )
    token = auth.login("my-school", "admin@institution.test", PASSWORD)
    space = Workspace(auth, token)
    assert space.exams() == [] and space.submissions() == []
    observed_modes = []
    original = subprocess.Popen

    def capture_mode(*args, **kwargs):
        observed_modes.append(kwargs["env"]["SMARTGRADE_MODE"])
        return original(*args, **kwargs)

    monkeypatch.setattr("subprocess.Popen", capture_mode)
    runtime = launch_institution(ROOT, sys.executable, auth.root, port=free_port(), timeout=30)
    try:
        assert runtime.web.process.poll() is None and runtime.worker.poll() is None
        assert observed_modes == ["commercial", "commercial"]
        eid = space.create_exam(
            ExamSpec(title="SYNTHETIC Actual Workflow", kind="mcq", answer_key=["A"] * 20)
        )
        sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
        space.approve_mcq(sid, ["A"] * 20, "", "", 1, True)
        jobs = Jobs(space)
        jid = jobs.create("report", {"exam_id": eid, "format": "csv"})
        deadline = time.monotonic() + 25
        while jobs.get(jid).state in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.1)
        assert jobs.get(jid).state == "succeeded"
        assert jobs.result_path(jid).is_file()
    finally:
        runtime.stop()
        runtime.stop()
    assert runtime.web.process.poll() is not None and runtime.worker.poll() is not None
    resumed = launch_institution(ROOT, sys.executable, auth.root, port=free_port(), timeout=30)
    try:
        assert space.submission(sid).total == 20 and len(space.exams()) == 1
        fresh = auth.login("my-school", "admin@institution.test", PASSWORD)
        assert Workspace(auth, fresh).submission(sid).total == 20
    finally:
        resumed.stop()
    key = create_key(tmp_path / "private.key")
    snapshot = backup_full(auth.root, tmp_path / "full.sgb", key, services_stopped=True)
    restore_full(snapshot, tmp_path / "restored", key)
    recovered = AuthService(tmp_path / "restored")
    with pytest.raises(AuthError):
        recovered.actor(token)
    restored_token = recovered.login("my-school", "admin@institution.test", PASSWORD)
    assert Workspace(recovered, restored_token).submission(sid).total == 20


def test_stopping_runtime_also_stops_active_worker_child(tmp_path):
    child_file = tmp_path / "child.pid"
    web = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)',
            str(child_file),
        ],
        start_new_session=True,
    )
    runtime = InstitutionRuntime(
        AppRuntime(web, 8502, tmp_path, tmp_path / "log"), worker, _start_time(worker.pid)
    )
    try:
        deadline = time.monotonic() + 5
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_file.exists()
        child = int(child_file.read_text())
        runtime.stop()
        child_stat = Path(f"/proc/{child}/stat")
        assert not child_stat.exists() or child_stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    finally:
        runtime.stop()


def test_institution_notebook_is_authenticated_and_contains_no_seed_or_password():
    notebook = build_institution_notebook()
    assert notebook["metadata"]["smartgrade_mode"] == "commercial"
    code = [c["source"] for c in notebook["cells"] if c["cell_type"] == "code"]
    for source in code:
        ast.parse(source)
    account = next(source for source in code if "identity.provision_organization(" in source)
    assert "getpass(" in account and "del password, confirmation" in account
    assert 'plan="school"' in account
    assert not any("scripts.generate_omr" in source for source in code)
    assert any("launch_institution(" in source for source in code)
    assert any("backup-full" in source and "--services-stopped" in source for source in code)
    assert any("restore-full" in source for source in code)
    assert all(c.get("outputs", []) == [] for c in notebook["cells"])
    assert not any("ngrok" in source for source in code)


def test_notebook_account_backup_restore_cells_execute_without_seeding_or_printing_password(
    tmp_path, monkeypatch, capsys
):
    import os
    import shutil
    import types
    import getpass

    notebook = build_institution_notebook()
    cells = [c["source"] for c in notebook["cells"] if c["cell_type"] == "code"]
    account = next(c for c in cells if "identity.provision_organization(" in c)
    backup = next(c for c in cells if "BACKUP_ACTION = " in c)
    restore = next(c for c in cells if "RESTORE_INSTITUTION = " in c)
    downloads = []
    colab = types.ModuleType("google.colab")
    colab.files = types.SimpleNamespace(download=lambda path: downloads.append(Path(path)))
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    inputs = iter(["real-school", "SYNTHETIC Notebook Institution", "admin@notebook.test", "Admin"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(getpass, "getpass", lambda prompt: PASSWORD)
    scope = {
        "os": os,
        "DATA_DIR": tmp_path / "data",
        "BACKUP_KEY_FILE": tmp_path / "backup.key",
        "CONTENT_ROOT": tmp_path,
        "PROJECT_DIR": ROOT,
        "PYTHON": Path(sys.executable),
        "subprocess": subprocess,
        "shutil": shutil,
        "Path": Path,
    }
    exec(account, scope)
    auth = AuthService(scope["DATA_DIR"])
    token = auth.login("real-school", "admin@notebook.test", PASSWORD)
    space = Workspace(auth, token)
    assert space.exams() == [] and space.submissions() == []
    assert len(downloads) == 1 and downloads[0].stat().st_size == 32
    assert PASSWORD not in capsys.readouterr().out

    def forbidden(prompt):
        pytest.fail("An existing organization must not get a new account/password")

    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(getpass, "getpass", forbidden)
    exec(account, scope)
    assert auth.actor(token).role == "admin"
    eid = space.create_exam(ExamSpec(title="SYNTHETIC Notebook Workflow", kind="mcq", answer_key=["A"] * 20))
    sid = space.upload(eid, "SYNTHETIC", png_bytes(template()))
    space.approve_mcq(sid, ["A"] * 20, "", "", 1, True)
    exec(backup.replace('BACKUP_ACTION = "none"', 'BACKUP_ACTION = "download"'), scope)
    snapshot = downloads[-1]
    assert snapshot.suffix == ".sgb" and snapshot.read_bytes().startswith(b"SGFULL2\n")
    original_key = scope["BACKUP_KEY_FILE"]
    scope["DATA_DIR"], scope["BACKUP_KEY_FILE"] = tmp_path / "restored", tmp_path / "restored.key"
    restore = restore.replace("RESTORE_INSTITUTION = False", "RESTORE_INSTITUTION = True")
    restore = restore.replace('RESTORE_ARCHIVE = ""', "RESTORE_ARCHIVE = " + repr(str(snapshot)))
    restore = restore.replace('RESTORE_KEY_FILE = ""', "RESTORE_KEY_FILE = " + repr(str(original_key)))
    exec(restore, scope)
    assert scope["_institution_restore_complete"]
    recovered = AuthService(scope["DATA_DIR"])
    with pytest.raises(AuthError):
        recovered.actor(token)
    new = recovered.login("real-school", "admin@notebook.test", PASSWORD)
    assert Workspace(recovered, new).submission(sid).total == 20
    exec(account, scope)
