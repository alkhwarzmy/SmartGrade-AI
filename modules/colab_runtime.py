"""Colab process lifecycle and portable backups, implemented with the standard library.

SQLite stays on the runtime's local disk. Google Drive is a destination for ZIP
snapshots, never a live WAL database. This module does not contact Google services.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
import time
from urllib.request import urlopen
import zipfile

MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_BACKUP_FILES = 20_000
IMAGE_NAME = re.compile(r"^[a-f0-9]{32}\.png$")


@dataclass
class AppRuntime:
    process: subprocess.Popen
    port: int
    data_dir: Path
    log_path: Path

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def launch_app(
    project_dir, python, data_dir, *, port=8501, browser_address="", extra_env=None, timeout=45, mode="demo"
):
    if mode not in {"demo", "commercial"}:
        raise ValueError("وضع التشغيل يجب أن يكون commercial أو demo")
    project_dir, data_dir = Path(project_dir).resolve(), Path(data_dir).resolve()
    python = Path(python).absolute()  # Keep a venv's symlink; resolve() would select its base interpreter.
    if not (project_dir / "app.py").is_file() or not python.is_file():
        raise ValueError("المشروع أو مفسر Python غير موجود")
    if not 1024 <= port <= 65535:
        raise ValueError("رقم المنفذ غير صالح")
    if data_dir.is_relative_to(Path("/content/drive")):
        raise ValueError("احفظ SQLite محليًا، واستخدم Google Drive للنسخ الاحتياطية فقط")
    if browser_address and not re.fullmatch(r"[a-zA-Z0-9.-]+", browser_address):
        raise ValueError("عنوان عرض المتصفح غير صالح")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise ValueError("المنفذ مستخدم؛ أوقف التشغيل السابق أو اختر منفذًا آخر") from exc
    data_dir.mkdir(parents=True, exist_ok=True)
    logs = project_dir / ".colab"
    logs.mkdir(exist_ok=True)
    log_path = logs / "streamlit.log"
    env = {
        **os.environ,
        "SMARTGRADE_DATA_DIR": str(data_dir),
        "SMARTGRADE_MODE": mode,
        "ALLOW_EXTERNAL_AI": "false",
        "ALLOW_PAID_API": "false",
        "OCR_PROVIDER": "tesseract" if shutil.which("tesseract") else "manual",
    }
    allowed = {
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "ALLOW_EXTERNAL_AI",
        "ALLOW_PAID_API",
        "OCR_PROVIDER",
    }
    if extra_env:
        if set(extra_env) - allowed:
            raise ValueError("إعداد إضافي غير مسموح")
        env.update(extra_env)
    command = [
        str(python),
        "-m",
        "streamlit",
        "run",
        str(project_dir / "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
    ]
    if browser_address:
        command += ["--browser.serverAddress", browser_address]
    with log_path.open("ab") as log:
        process = subprocess.Popen(command, cwd=project_dir, env=env, stdout=log, stderr=log)
    runtime = AppRuntime(process, port, data_dir, log_path)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("تعذر بدء التطبيق؛ راجع سجل التشغيل المحلي")
            try:
                with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                    if response.status == 200 and response.read(16).strip() == b"ok":
                        return runtime
            except OSError:
                pass
            time.sleep(0.15)
        raise RuntimeError("انتهت مهلة تشغيل التطبيق")
    except BaseException:
        runtime.stop()
        raise


def _safe_member(name):
    if "\\" in name:
        raise ValueError("مسار غير صالح داخل النسخة")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ValueError("مسار غير صالح داخل النسخة")
    if name in {"smartgrade.sqlite3", "backup-manifest.json"}:
        return
    if len(path.parts) != 2 or path.parts[0] != "images" or not IMAGE_NAME.fullmatch(path.name):
        raise ValueError("ملف غير متوقع داخل النسخة")


def _check_database(path):
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("قاعدة البيانات تالفة")
        required = {"exams", "submissions", "review_history"}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(tables) or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("مخطط قاعدة البيانات أو العلاقات غير صالح")
        for original, aligned in db.execute("SELECT image_name, aligned_name FROM submissions"):
            for name in (original, aligned):
                if name is None:
                    continue
                if not IMAGE_NAME.fullmatch(name) or not (path.parent / "images" / name).is_file():
                    raise ValueError("صورة مرتبطة بقاعدة البيانات مفقودة أو غير صالحة")


def backup_data(data_dir, destination, *, app_stopped=False):
    """Create a consistent database+images ZIP. Caller must stop all writers first."""
    if not app_stopped:
        raise ValueError("أوقف التطبيق قبل النسخ لحفظ الصور وقاعدة البيانات معًا")
    data_dir, destination = Path(data_dir).resolve(), Path(destination).absolute()
    database = data_dir / "smartgrade.sqlite3"
    if not database.is_file():
        raise ValueError("لا توجد قاعدة بيانات لنسخها")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("اختر اسمًا جديدًا؛ لن تُستبدل نسخة احتياطية موجودة")
    with tempfile.TemporaryDirectory(prefix="smartgrade-backup-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        images = stage / "images"
        images.mkdir()
        for image in sorted((data_dir / "images").glob("*")):
            if image.is_symlink() or not image.is_file() or not IMAGE_NAME.fullmatch(image.name):
                raise ValueError("ملف صورة غير متوقع؛ ألغيت النسخة")
            shutil.copyfile(image, images / image.name)
        # SQLite's backup API incorporates committed WAL content, unlike copying only the .sqlite3 file.
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
            with sqlite3.connect(stage / "smartgrade.sqlite3") as target:
                source.backup(target)
        _check_database(stage / "smartgrade.sqlite3")
        entries = [stage / "smartgrade.sqlite3", *sorted(images.glob("*.png"))]
        if len(entries) + 1 > MAX_BACKUP_FILES or sum(p.stat().st_size for p in entries) > MAX_BACKUP_BYTES:
            raise ValueError("البيانات أكبر من حدود النسخة المحمولة؛ قسّمها أو استخدم نسخًا مخصصًا")
        hashes = {
            p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in entries
        }
        manifest = {
            "format": "smartgrade-backup-v1",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": hashes,
        }
        (stage / "backup-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        archive = stage / "snapshot.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for entry in [*entries, stage / "backup-manifest.json"]:
                bundle.write(entry, entry.relative_to(stage).as_posix())
        # Exclusive creation avoids silently replacing an existing snapshot.
        with destination.open("xb") as output, archive.open("rb") as source:
            shutil.copyfileobj(source, output)
    return destination


def restore_data(archive, data_dir):
    """Restore a trusted SmartGrade snapshot into an EMPTY data directory only."""
    archive, data_dir = Path(archive).resolve(), Path(data_dir).absolute()
    if data_dir.is_symlink() or (data_dir.exists() and any(data_dir.iterdir())):
        raise ValueError("الاستعادة تتطلب مجلد بيانات فارغًا؛ لن تُستبدل بيانات موجودة")
    if data_dir.is_relative_to(Path("/content/drive")):
        raise ValueError("استعد البيانات على قرص Colab المحلي، وليس Google Drive")
    if archive.stat().st_size > MAX_BACKUP_BYTES:
        raise ValueError("حجم النسخة يتجاوز الحد المسموح")
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smartgrade-restore-", dir=data_dir.parent) as temporary:
        stage = Path(temporary) / "data"
        stage.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_BACKUP_FILES or sum(i.file_size for i in infos) > MAX_BACKUP_BYTES:
                raise ValueError("حجم النسخة بعد الفك أو عدد ملفاتها غير مقبول")
            names = set()
            for info in infos:
                _safe_member(info.filename)
                mode = info.external_attr >> 16
                if info.filename in names or info.is_dir() or stat.S_ISLNK(mode):
                    raise ValueError("ملف مكرر أو رابط غير مسموح")
                names.add(info.filename)
                target = stage / info.filename
                target.parent.mkdir(exist_ok=True)
                with bundle.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        manifest = json.loads((stage / "backup-manifest.json").read_text())
        if manifest.get("format") != "smartgrade-backup-v1":
            raise ValueError("تنسيق النسخة غير مدعوم")
        hashes = manifest.get("sha256", {})
        if (
            not isinstance(hashes, dict)
            or set(hashes) != names - {"backup-manifest.json"}
            or "smartgrade.sqlite3" not in hashes
        ):
            raise ValueError("قائمة الملفات لا تطابق النسخة")
        for name, expected in hashes.items():
            if hashlib.sha256((stage / name).read_bytes()).hexdigest() != expected:
                raise ValueError("بصمة الملف لا تطابق النسخة")
        (stage / "images").mkdir(exist_ok=True)
        _check_database(stage / "smartgrade.sqlite3")
        (stage / "backup-manifest.json").unlink()
        if data_dir.exists():
            data_dir.rmdir()  # Fails safely if a concurrent writer created data.
        os.replace(stage, data_dir)
    return data_dir
