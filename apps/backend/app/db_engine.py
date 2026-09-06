"""Engine/session plumbing for the SQLAlchemy data layer (SQLite & PostgreSQL support).

Every ``Database`` instance owns its own engines (one async for the document
tables, one sync for the encrypted ``api_keys`` table read on the synchronous
LLM hot path) built from these factories.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.models import Base

__all__ = ["Base", "make_async_engine", "make_sync_engine", "init_models_sync"]


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Set per-connection SQLite PRAGMAs.

    WAL improves concurrent read/write between the async (doc tables) and sync
    (api_keys) engines pointed at the same file; ``busy_timeout`` rides out the
    brief lock contention that creates; ``foreign_keys`` enforces relational
    integrity (off by default in SQLite).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _url(path: Path, *, driver: str) -> str:
    """Build a SQLite URL. Absolute paths yield the required four slashes."""
    return f"sqlite+{driver}:///{path}" if driver else f"sqlite:///{path}"


def make_async_engine(db_target: str | Path) -> AsyncEngine:
    """Create the async engine (aiosqlite for SQLite or asyncpg for PostgreSQL)."""
    if isinstance(db_target, str) and (db_target.startswith("postgresql://") or db_target.startswith("postgres://")):
        url = db_target.replace("postgres://", "postgresql://", 1)
        if not url.startswith("postgresql+asyncpg://") and not url.startswith("postgresql+psycopg://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return create_async_engine(url, future=True, pool_pre_ping=True)

    path = Path(db_target) if isinstance(db_target, (str, Path)) else db_target
    engine = create_async_engine(_url(path, driver="aiosqlite"), future=True)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def make_sync_engine(db_target: str | Path) -> Engine:
    """Create the sync engine used for the encrypted api_keys table."""
    if isinstance(db_target, str) and (db_target.startswith("postgresql://") or db_target.startswith("postgres://")):
        url = db_target.replace("postgres://", "postgresql://", 1)
        if not url.startswith("postgresql+psycopg2://") and not url.startswith("postgresql+psycopg://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return create_engine(url, future=True, pool_pre_ping=True)

    path = Path(db_target) if isinstance(db_target, (str, Path)) else db_target
    engine = create_engine(_url(path, driver=""), future=True)
    event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def init_models_sync(engine: Engine) -> None:
    """Create all tables (idempotent) using a sync engine connection."""
    Base.metadata.create_all(engine)

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            columns = conn.exec_driver_sql("PRAGMA table_info(resumes)").mappings().all()
            if columns and "interview_prep" not in {column["name"] for column in columns}:
                conn.exec_driver_sql("ALTER TABLE resumes ADD COLUMN interview_prep TEXT")

