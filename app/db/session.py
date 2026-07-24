from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


def database_backend(database_url: str) -> str:
    """Return the SQLAlchemy backend name for a database URL."""

    return make_url(database_url).get_backend_name()


def database_engine_options(
    database_url: str,
    *,
    connect_timeout_seconds: int,
) -> dict[str, Any]:
    """Build backend-specific SQLAlchemy engine options."""

    url = make_url(database_url)
    backend = url.get_backend_name()
    options: dict[str, Any] = {"pool_pre_ping": True}

    if backend == "postgresql":
        options["connect_args"] = {"connect_timeout": connect_timeout_seconds}
        return options

    if backend == "sqlite":
        options["connect_args"] = {
            "check_same_thread": False,
            "timeout": connect_timeout_seconds,
        }
        if url.database in {None, "", ":memory:"}:
            options["poolclass"] = StaticPool

    return options


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_database_engine(
    database_url: str,
    *,
    connect_timeout_seconds: int = 5,
) -> Engine:
    """Create a configured SQLAlchemy engine for PostgreSQL or SQLite."""

    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)

    db_engine = create_engine(
        database_url,
        **database_engine_options(
            database_url,
            connect_timeout_seconds=connect_timeout_seconds,
        ),
    )
    if database_backend(database_url) == "sqlite":
        event.listen(db_engine, "connect", _enable_sqlite_foreign_keys)
    return db_engine


engine = create_database_engine(
    settings.database_url,
    connect_timeout_seconds=settings.db_connect_timeout_seconds,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
