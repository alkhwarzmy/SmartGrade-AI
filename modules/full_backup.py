"""Offline complete snapshots, streamed AES-256-GCM, manifests and atomic restore into an empty path."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import zipfile
from datetime import datetime, timezone
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidTag

MAGIC = b"SGFULL2\n"
MAX_BYTES = 20 * 1024**3
MAX_FILES = 300_000
HEX = re.compile(r"^[a-f0-9]{32}$")


def create_key(path):
    path = Path(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(os.urandom(32))
    return path


def read_key(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("مفتاح النسخ يجب أن يكون ملفًا خاصًا بالمالك (chmod 600)")
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("مفتاح AES يجب أن يكون 32 بايتًا")
    return key


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name:
        raise ValueError("مسار غير آمن داخل النسخة")
    if name in {"identity.sqlite3", "manifest.json"}:
        return
    parts = path.parts
    if len(parts) < 3 or parts[0] != "organizations" or not HEX.fullmatch(parts[1]):
        raise ValueError("ملف غير مسموح في النسخة")
    if len(parts) == 3 and parts[2] == "smartgrade.sqlite3":
        return
    if len(parts) == 4 and parts[2] in {"images", "job-results"}:
        suffix = path.suffix
        if HEX.fullmatch(path.stem) and suffix in (
            {".png"} if parts[2] == "images" else {".csv", ".xlsx", ".pdf"}
        ):
            return
    raise ValueError("ملف غير مسموح في النسخة")


def _check(stage):
    with closing(sqlite3.connect(stage / "identity.sqlite3")) as db:
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("هوية تالفة أو علاقات غير صالحة")
        if db.execute("PRAGMA user_version").fetchone()[0] not in (1, 2):
            raise ValueError("إصدار مخطط الهوية غير مدعوم")
        organizations = {r[0] for r in db.execute("SELECT id FROM organizations")}
    for folder in (stage / "organizations").glob("*"):
        if folder.name not in organizations or not HEX.fullmatch(folder.name):
            raise ValueError("مؤسسة بدون مرجع هوية")
        path = folder / "smartgrade.sqlite3"
        if not path.exists():
            raise ValueError("قاعدة مؤسسة مفقودة")
        with closing(sqlite3.connect(path)) as db:
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("قاعدة مؤسسة تالفة")
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
                raise ValueError("إصدار قاعدة المؤسسة غير مدعوم")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            pending = (
                {r[0] for r in db.execute("SELECT id FROM pending_deletions")}
                if "pending_deletions" in tables
                else set()
            )
            for sid, image, aligned in db.execute("SELECT id,image_name,aligned_name FROM submissions"):
                for name in (image, aligned):
                    if name and (
                        not re.fullmatch(r"[a-f0-9]{32}\.png", name)
                        or (sid not in pending and not (folder / "images" / name).is_file())
                    ):
                        raise ValueError("صورة مرتبطة مفقودة أو غير صالحة")
    return organizations


def _encrypt(source, target, key):
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(MAGIC)
    target.write(MAGIC + nonce)
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            target.write(cipher.update(chunk))
    target.write(cipher.finalize())
    target.write(cipher.tag)


def backup_full(root, destination, key_file, *, services_stopped=False):
    if not services_stopped:
        raise ValueError("أوقف web وAPI والعامل قبل النسخ الكامل")
    root, destination = Path(root).resolve(), Path(destination).absolute()
    destination = destination.parent.resolve() / destination.name
    if destination.is_relative_to(root) or Path(key_file).resolve().is_relative_to(root):
        raise ValueError("احفظ النسخة ومفتاحها خارج مجلد التشغيل")
    if destination.exists():
        raise ValueError("لن تُستبدل نسخة موجودة")
    key = read_key(key_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smartgrade-full-", dir=destination.parent) as temp:
        stage = Path(temp) / "data"
        stage.mkdir()
        entries = [root / "identity.sqlite3"]
        for folder in (root / "organizations").glob("*"):
            if folder.is_symlink() or not HEX.fullmatch(folder.name):
                raise ValueError("مجلد مؤسسة غير متوقع")
            if not (folder / "smartgrade.sqlite3").exists():
                continue
            entries.append(folder / "smartgrade.sqlite3")
            for subfolder in ("images", "job-results"):
                entries.extend((folder / subfolder).glob("*"))
        if len(entries) + 1 > MAX_FILES or sum(p.stat().st_size for p in entries) > MAX_BYTES:
            raise ValueError("حجم النسخة يتجاوز الحد المعلن 20 GiB أو 300000 ملف")
        if (
            shutil.disk_usage(destination.parent).free
            < 3 * sum(p.stat().st_size for p in entries) + 64 * 1024**2
        ):
            raise ValueError("مساحة تجهيز النسخة غير كافية؛ يلزم احتياط يصل إلى ثلاثة أمثال حجم البيانات")
        for source in entries:
            name = source.relative_to(root).as_posix()
            _member(name)
            if (
                any(p.is_symlink() for p in [source, *source.parents] if p.is_relative_to(root))
                or not source.is_file()
            ):
                raise ValueError("لا تُنسخ الروابط الرمزية")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".sqlite3":
                with (
                    closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as db,
                    closing(sqlite3.connect(target)) as output,
                ):
                    db.backup(output)
            else:
                shutil.copyfile(source, target)
        _check(stage)
        hashes = {p.relative_to(stage).as_posix(): digest(p) for p in stage.rglob("*") if p.is_file()}
        manifest = {
            "format": "smartgrade-full-v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashes,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest))
        if sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) > MAX_BYTES:
            raise ValueError("حجم البيانات والفهرس يتجاوز حد النسخة")
        archive = Path(temp) / "snapshot.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in stage.rglob("*"):
                if path.is_file():
                    bundle.write(path, path.relative_to(stage).as_posix())
        encrypted = Path(temp) / "complete.sgb"
        fd = os.open(encrypted, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            _encrypt(archive, target, key)
            target.flush()
            os.fsync(target.fileno())
        if encrypted.stat().st_size > MAX_BYTES:
            raise ValueError("حجم النسخة المشفرة يتجاوز الحد؛ لم تُنشر نسخة غير قابلة للاستعادة")
        # Same-filesystem hard link publishes only a complete authenticated file, without replacing anything.
        os.link(encrypted, destination)
        parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    return destination


def restore_full(archive, destination, key_file):
    archive, destination = Path(archive), Path(destination).absolute()
    if destination.is_symlink() or destination.exists():
        raise ValueError("اختر مجلد استعادة جديدًا؛ لن تُستبدل بيانات موجودة")
    key = read_key(key_file)
    if not 36 <= archive.stat().st_size <= MAX_BYTES:
        raise ValueError("حجم نسخة غير مقبول")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smartgrade-restore-", dir=destination.parent) as temp:
        plain = Path(temp) / "snapshot.zip"
        with archive.open("rb") as source, plain.open("wb") as target:
            if source.read(len(MAGIC)) != MAGIC:
                raise ValueError("صيغة النسخة غير مدعومة")
            nonce = source.read(12)
            source.seek(-16, 2)
            tag = source.read(16)
            remaining = archive.stat().st_size - len(MAGIC) - 12 - 16
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            cipher.authenticate_additional_data(MAGIC)
            source.seek(len(MAGIC) + 12)
            try:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("نسخة مبتورة")
                    remaining -= len(chunk)
                    target.write(cipher.update(chunk))
                target.write(cipher.finalize())
            except InvalidTag as exc:
                raise ValueError("فشل توثيق النسخة؛ مفتاح خاطئ أو ملف معدل") from exc
        stage = Path(temp) / "data"
        stage.mkdir()
        with zipfile.ZipFile(plain) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_BYTES:
                raise ValueError("النسخة تتجاوز حدود الفك")
            if shutil.disk_usage(destination.parent).free < sum(i.file_size for i in infos) + 64 * 1024**2:
                raise ValueError("مساحة الاستعادة غير كافية")
            names = set()
            for info in infos:
                _member(info.filename)
                if (
                    info.filename in names
                    or info.is_dir()
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("ملف مكرر أو رابط غير صالح")
                names.add(info.filename)
                target = stage / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        manifest = json.loads((stage / "manifest.json").read_text())
        if manifest.get("format") != "smartgrade-full-v2" or set(manifest["sha256"]) != names - {
            "manifest.json"
        }:
            raise ValueError("قائمة ملفات النسخة غير متطابقة")
        for name, expected in manifest["sha256"].items():
            if digest(stage / name) != expected:
                raise ValueError("بصمة ملف غير متطابقة")
        organizations = _check(stage)
        from modules.auth import AuthService

        # Preserve schema versions: a pre-upgrade snapshot must still be usable by its original release.
        with closing(sqlite3.connect(stage / "identity.sqlite3")) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions")
            for oid in organizations:
                AuthService._audit(
                    SimpleNamespace(clock=time.time),
                    db,
                    oid,
                    "operator",
                    "installation.restored_sessions_revoked",
                )
            db.commit()
        for folder in (stage / "organizations").glob("*"):
            with closing(sqlite3.connect(folder / "smartgrade.sqlite3")) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "jobs" in tables:
                    db.execute(
                        "UPDATE jobs SET state='failed',error='restored_retry_required' WHERE state IN ('queued','running')"
                    )
                    db.commit()
        (stage / "manifest.json").unlink()
        # Restrict restored PII before publishing the fully verified directory.
        for path in stage.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        stage.chmod(0o700)
        os.rename(stage, destination)
    return destination
