from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.db import startup
from app.db.session import (
    create_database_engine,
    database_backend,
    database_engine_options,
)


def test_file_sqlite_connection_persists_data_and_enforces_foreign_keys(tmp_path) -> None:
    database_path = tmp_path / "lm-agent.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE parents (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE children ("
                "id INTEGER PRIMARY KEY, "
                "parent_id INTEGER NOT NULL REFERENCES parents(id)"
                ")"
            )
        )
        connection.execute(text("INSERT INTO parents (id) VALUES (1)"))
        connection.execute(text("INSERT INTO children (id, parent_id) VALUES (1, 1)"))

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM children")).scalar_one() == 1
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO children (id, parent_id) VALUES (2, 999)"))

    engine.dispose()


def test_memory_sqlite_connection_is_shared_across_threads() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    assert isinstance(engine.pool, StaticPool)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE checks (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO checks (value) VALUES ('connected')"))

    def read_value() -> str:
        with engine.connect() as connection:
            return connection.execute(text("SELECT value FROM checks")).scalar_one()

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(read_value).result() == "connected"

    engine.dispose()


def test_postgresql_options_remain_supported() -> None:
    options = database_engine_options(
        "postgresql+psycopg://user:password@localhost/lm_agent",
        connect_timeout_seconds=9,
    )

    assert database_backend("postgresql+psycopg://user:password@localhost/lm_agent") == "postgresql"
    assert options == {
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 9},
    }


def test_sqlite_options_allow_fastapi_thread_usage() -> None:
    options = database_engine_options(
        "sqlite:///data/lm_agent.sqlite3",
        connect_timeout_seconds=7,
    )

    assert database_backend("sqlite:///data/lm_agent.sqlite3") == "sqlite"
    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {
        "check_same_thread": False,
        "timeout": 7,
    }


def test_startup_check_initializes_missing_sqlite_schema(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "startup.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(startup, "engine", engine)

    startup.ensure_database_ready()

    with engine.connect() as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        document_columns = {
            row[1]: bool(row[3]) for row in connection.execute(text("PRAGMA table_info(documents)"))
        }
        chat_session_columns = {
            row[1]: bool(row[3])
            for row in connection.execute(text("PRAGMA table_info(chat_sessions)"))
        }
        analysis_file_columns = {
            row[1]: bool(row[3])
            for row in connection.execute(text("PRAGMA table_info(analysis_files)"))
        }
    assert {
        "documents",
        "document_chunks",
        "chat_sessions",
        "workspaces",
        "workspace_permissions",
        "analysis_artifacts",
    }.issubset(table_names)
    assert document_columns["session_id"] is False
    assert document_columns["knowledge_base_id"] is False
    assert chat_session_columns["workspace_id"] is False
    assert analysis_file_columns["workspace_id"] is True
    engine.dispose()


def test_startup_check_upgrades_legacy_sqlite_document_scope(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(startup, "engine", engine)
    startup.ensure_database_ready()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("DROP TABLE documents")
        connection.exec_driver_sql(
            "CREATE TABLE documents (id CHAR(32) PRIMARY KEY, knowledge_base_id CHAR(32) NOT NULL)"
        )

    startup.ensure_database_ready()

    with engine.connect() as connection:
        document_columns = {
            row[1]: bool(row[3]) for row in connection.execute(text("PRAGMA table_info(documents)"))
        }
    assert document_columns["session_id"] is False
    assert document_columns["knowledge_base_id"] is False
    assert "filename" in document_columns
    engine.dispose()
