"""Engine/session plumbing for the SQLAlchemy data layer (SQLite & PostgreSQL).

Every ``Database`` instance owns its own engines (one async for the document
tables, one sync for the encrypted ``api_keys`` table read on the synchronous
LLM hot path) built from these factories. Keeping construction here lets tests
spin up fully isolated engines against a temp-file database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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


def _is_postgres_target(db_target: str | Path) -> bool:
    if not isinstance(db_target, str):
        return False
    return db_target.startswith("postgresql://") or db_target.startswith("postgres://")


def _ensure_query_param(url: str, key: str, value: str) -> str:
    """Add a query parameter if it is not already present."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if key in query or key.lower() in {k.lower() for k in query}:
        return url
    query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def _normalize_postgres_url(db_target: str, *, driver: str) -> str:
    """Normalize a Postgres URL for SQLAlchemy and managed hosts (Supabase)."""
    url = db_target.replace("postgres://", "postgresql://", 1)
    prefix = f"postgresql+{driver}://"
    if not url.startswith("postgresql+"):
        url = url.replace("postgresql://", prefix, 1)
    # psycopg2 understands sslmode=; asyncpg rejects it as a connect kwarg.
    if driver == "psycopg2" and (
        "supabase.com" in url or "sslmode" not in url.lower()
    ):
        url = _ensure_query_param(url, "sslmode", "require")
    if driver == "asyncpg":
        parsed = urlparse(url)
        query = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() != "sslmode"
        ]
        url = urlunparse(parsed._replace(query=urlencode(query)))
    return url


def make_async_engine(db_target: str | Path) -> AsyncEngine:
    """Create the async engine (aiosqlite for SQLite or asyncpg for PostgreSQL)."""
    if _is_postgres_target(db_target):
        import ssl

        url = _normalize_postgres_url(str(db_target), driver="asyncpg")
        # Encrypted TLS to Supabase; disable hostname/CA strictness because some
        # Render/runtime CA bundles fail on the pooler chain (CERT_VERIFY_FAILED).
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args: dict[str, Any] = {"ssl": ssl_context}
        # Supabase transaction pooler is incompatible with prepared-statement cache.
        if "pooler.supabase.com" in url:
            connect_args["statement_cache_size"] = 0
        return create_async_engine(
            url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )

    path = Path(db_target)
    engine = create_async_engine(_url(path, driver="aiosqlite"), future=True)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def make_sync_engine(db_target: str | Path) -> Engine:
    """Create the sync engine used for the encrypted api_keys table."""
    if _is_postgres_target(db_target):
        url = _normalize_postgres_url(str(db_target), driver="psycopg2")
        return create_engine(url, future=True, pool_pre_ping=True)

    path = Path(db_target)
    engine = create_engine(_url(path, driver=""), future=True)
    event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def init_models_sync(engine: Engine) -> None:
    """Create all tables (idempotent) using a sync engine connection."""
    Base.metadata.create_all(engine)

    # ``create_all`` does not ALTER existing SQLite tables. Keep these additive
    # migrations idempotent so older local databases can load resumes safely.
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        columns = conn.exec_driver_sql("PRAGMA table_info(resumes)").mappings().all()
        existing_columns = {column["name"] for column in columns}
        if columns and "interview_prep" not in existing_columns:
            conn.exec_driver_sql("ALTER TABLE resumes ADD COLUMN interview_prep TEXT")
        if columns and "processing_token" not in existing_columns:
            conn.exec_driver_sql("ALTER TABLE resumes ADD COLUMN processing_token TEXT")

        preview_columns = (
            conn.exec_driver_sql("PRAGMA table_info(tailoring_previews)").mappings().all()
        )
        if preview_columns and "improvements" not in {
            column["name"] for column in preview_columns
        }:
            conn.exec_driver_sql(
                "ALTER TABLE tailoring_previews ADD COLUMN improvements JSON"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_preview_compatibility "
            "ON tailoring_previews (source_id, job_id, payload_hash, created_at)"
        )
