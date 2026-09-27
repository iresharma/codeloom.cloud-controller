from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def init_db(database_url: str) -> None:
    global _engine, SessionLocal
    if _engine is not None:
        _engine.dispose()
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    _engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _connection_record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    from codeloom_cloud import models  # noqa: F401

    Base.metadata.create_all(_engine)


def open_session() -> Session:
    if SessionLocal is None:
        raise RuntimeError("database is not initialized")
    return SessionLocal()


def get_db() -> Iterator[Session]:
    db = open_session()
    try:
        yield db
    finally:
        db.close()
