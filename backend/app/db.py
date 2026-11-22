"""Database engine, sessions and migrations (SQLite, single local user).

The API process and the worker process both open this database. WAL mode lets one of them write while
the other reads, and a busy timeout makes the rare write collision wait instead of failing.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """SQLite has no timezone type: store naive UTC, always hand back timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        return None if value is None else value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def configure(db_path: Path | str) -> Engine:
    """Create (or replace) the process-wide engine for the given SQLite file."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    url = "sqlite://" if str(db_path) == ":memory:" else f"sqlite:///{Path(db_path).as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    _engine = engine
    _factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database is not configured; call db.configure() first")
    return _engine


def new_session() -> Session:
    if _factory is None:
        raise RuntimeError("database is not configured; call db.configure() first")
    return _factory()


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """A short transaction: commits on success, rolls back on any error."""
    session = new_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def run_migrations(db_path: Path, lock_dir: Path) -> None:
    """Bring the schema to the latest revision. Safe to call from several processes at once."""
    from alembic import command
    from alembic.config import Config
    from filelock import FileLock

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(backend_dir / "alembic").replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}".replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    lock_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_dir / ".migrate.lock"), timeout=120):
        command.upgrade(cfg, "head")
