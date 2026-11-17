from pathlib import Path
from sqlalchemy import create_engine, event
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

    Base.metadata.create_all(engine)
    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
