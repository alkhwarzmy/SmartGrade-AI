from pathlib import Path
import socket
import stat
import sys
import zipfile
import pytest
from modules.colab_runtime import backup_data, restore_data, launch_app
from modules.mcq_grader import template
from modules.preprocessing import png_bytes
from modules.review import Store
from modules.schemas import ExamSpec


def seed(root):
    store = Store(root)
    eid = store.create_exam(ExamSpec(title="اختبار نسخة", kind="mcq", answer_key=["A"] * 20))
    sid = store.upload(eid, "SYNTHETIC-BACKUP", png_bytes(template({0: ["A"]}, True)))
    store.approve_mcq(sid, ["A"] + [None] * 19, "معلم", "عينة اصطناعية", 1, True)
    return store, sid


def test_backup_restore_retains_grades_images_and_history(tmp_path):
    store, sid = seed(tmp_path / "original")
    archive = backup_data(store.root, tmp_path / "snapshot.zip", app_stopped=True)
    with zipfile.ZipFile(archive) as bundle:
        assert not any(name.endswith(("-wal", "-shm", ".env")) for name in bundle.namelist())
    restore_data(archive, tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.submission(sid).total == 1
    assert restored.image_path(restored.submission(sid).image_name).is_file()
    assert len(restored.history(sid)) == 2
    with pytest.raises(ValueError, match="فارغ"):
        restore_data(archive, restored.root)
    with pytest.raises(ValueError):
        backup_data(store.root, tmp_path / "snapshot.zip", app_stopped=True)
    with pytest.raises(ValueError, match="أوقف"):
        backup_data(store.root, tmp_path / "another.zip")


@pytest.mark.parametrize(
    "name", ["../escape", "/tmp/escape", "images/../../escape", "images\\bad.png", ".env"]
)
def test_restore_rejects_unexpected_paths(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, b"not allowed")
    with pytest.raises(ValueError):
        restore_data(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_tampered_image(tmp_path):
    store, _ = seed(tmp_path / "original")
    archive = backup_data(store.root, tmp_path / "snapshot.zip", app_stopped=True)
    bad = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"tampered" if name.endswith(".png") else source.read(name))
    with pytest.raises(ValueError, match="بصمة"):
        restore_data(bad, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_symlinks(tmp_path):
    archive = tmp_path / "bad.zip"
    info = zipfile.ZipInfo("smartgrade.sqlite3")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError):
        restore_data(archive, tmp_path / "restored")


def test_launch_health_and_stop(tmp_path):
    # Exercise the actual Streamlit lifecycle with isolated data and a free loopback port.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    runtime = launch_app(root, sys.executable, tmp_path / "runtime-data", port=port, timeout=30)
    try:
        assert runtime.process.poll() is None
        with pytest.raises(ValueError, match="المنفذ"):
            launch_app(root, sys.executable, tmp_path / "other", port=port)
    finally:
        runtime.stop()
    assert runtime.process.poll() is not None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux TCP TIME_WAIT regression")
def test_launch_reuses_closed_port_without_treating_time_wait_as_a_live_server(tmp_path):
    # A real accepted TCP connection forces the server endpoint into TIME_WAIT.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = listener.accept()
            connection.close()
            assert client.recv(1) == b""
    root = Path(__file__).resolve().parents[1]
    runtime = launch_app(root, sys.executable, tmp_path / "reused-port", port=port, timeout=30)
    try:
        assert runtime.process.poll() is None
        with pytest.raises(ValueError, match="المنفذ"):
            launch_app(root, sys.executable, tmp_path / "occupied", port=port)
    finally:
        runtime.stop()
