from pathlib import Path
import fcntl
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from config import DATA_DIR
from database.models import Base


def make_engine(root: Path = DATA_DIR):
    root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{root / 'smartgrade.sqlite3'}", connect_args={"timeout": 20}, hide_parameters=True
    )

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    with (root / ".schema.lock").open("a") as migration_lock:
        fcntl.flock(migration_lock, fcntl.LOCK_EX)
        with engine.connect() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            if version > 2:
                engine.dispose()
                raise RuntimeError("قاعدة المؤسسة أحدث من إصدار التطبيق؛ استعد نسخة متوافقة")
        # v0/v1 -> v2 is additive: preserve every existing grade/history and add durable work tables.
        assets_missing = not inspect(engine).has_table("file_assets")
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS submission_exam_status ON submissions(exam_id,status,created_at)"
            )
            connection.exec_driver_sql("PRAGMA user_version=2")
            if assets_missing:
                for folder in ("images", "job-results"):
                    for path in (root / folder).glob("*"):
                        if path.is_file() and not path.is_symlink():
                            connection.exec_driver_sql(
                                "INSERT OR IGNORE INTO file_assets(name,size) VALUES(?,?)",
                                (f"{folder}/{path.name}", path.stat().st_size),
                            )
    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
