import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.base import Base
from app.db.init_db import init_db
from app.db.session import database_backend, engine
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_image import DocumentImage


logger = logging.getLogger(__name__)


def ensure_database_ready() -> None:
    """Verify connectivity and initialize or upgrade the application schema."""

    import app.models  # noqa: F401

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Database connection check failed: {exc}") from exc

    backend = database_backend(str(engine.url))
    if backend == "sqlite":
        _ensure_sqlite_schema()
    else:
        _ensure_postgresql_schema()
    _validate_schema()
    logger.info("database_schema_ready", extra={"database_backend": backend})


def _ensure_postgresql_schema() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    document_columns = (
        {column["name"]: column for column in inspector.get_columns("documents")}
        if "documents" in tables
        else {}
    )
    chunk_columns = (
        {column["name"]: column for column in inspector.get_columns("document_chunks")}
        if "document_chunks" in tables
        else {}
    )
    image_columns = (
        {column["name"]: column for column in inspector.get_columns("document_images")}
        if "document_images" in tables
        else {}
    )
    session_fk_exists = "documents" in tables and any(
        foreign_key.get("constrained_columns") == ["session_id"]
        for foreign_key in inspector.get_foreign_keys("documents")
    )
    scope_check_exists = "documents" in tables and any(
        check.get("name") == "ck_documents_exactly_one_scope"
        for check in inspector.get_check_constraints("documents")
    )
    needs_upgrade = (
        not set(Base.metadata.tables).issubset(tables)
        or "session_id" not in document_columns
        or not document_columns.get("knowledge_base_id", {}).get("nullable", False)
        or not chunk_columns.get("knowledge_base_id", {}).get("nullable", False)
        or not image_columns.get("knowledge_base_id", {}).get("nullable", False)
        or not session_fk_exists
        or not scope_check_exists
    )
    if needs_upgrade:
        init_db()


def _ensure_sqlite_schema() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    tables_to_upgrade = (
        (Document.__table__, "knowledge_base_id"),
        (DocumentChunk.__table__, "knowledge_base_id"),
        (DocumentImage.__table__, "knowledge_base_id"),
    )
    for table, nullable_column in tables_to_upgrade:
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        needs_session_column = table.name == "documents" and "session_id" not in columns
        needs_nullable_column = not columns[nullable_column]["nullable"]
        if needs_session_column or needs_nullable_column:
            _rebuild_sqlite_table(table)
            inspector = inspect(engine)


def _rebuild_sqlite_table(table) -> None:
    old_name = f"_{table.name}_pre_session_scope"
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
        connection.commit()
        try:
            with connection.begin():
                old_columns = {
                    column["name"] for column in inspect(connection).get_columns(table.name)
                }
                for index in inspect(connection).get_indexes(table.name):
                    connection.exec_driver_sql(f'DROP INDEX IF EXISTS "{index["name"]}"')
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" RENAME TO "{old_name}"'
                )
                table.create(bind=connection)
                common_columns = [column.name for column in table.columns if column.name in old_columns]
                quoted = ", ".join(f'"{name}"' for name in common_columns)
                connection.exec_driver_sql(
                    f'INSERT INTO "{table.name}" ({quoted}) '
                    f'SELECT {quoted} FROM "{old_name}"'
                )
                connection.exec_driver_sql(f'DROP TABLE "{old_name}"')
        finally:
            connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _validate_schema() -> None:
    inspector = inspect(engine)
    missing_tables = set(Base.metadata.tables) - set(inspector.get_table_names())
    if missing_tables:
        raise RuntimeError(
            "Database schema initialization failed; missing tables: "
            + ", ".join(sorted(missing_tables))
        )
    document_column_details = {
        column["name"]: column for column in inspector.get_columns("documents")
    }
    document_columns = set(document_column_details)
    if "session_id" not in document_columns:
        raise RuntimeError("Database schema initialization failed; documents.session_id is missing.")
    nullable_targets = {
        "documents": document_column_details,
        "document_chunks": {
            column["name"]: column for column in inspector.get_columns("document_chunks")
        },
        "document_images": {
            column["name"]: column for column in inspector.get_columns("document_images")
        },
    }
    invalid = [
        f"{table_name}.knowledge_base_id"
        for table_name, columns in nullable_targets.items()
        if not columns["knowledge_base_id"]["nullable"]
    ]
    if invalid:
        raise RuntimeError(
            "Database schema initialization failed; columns must be nullable: "
            + ", ".join(invalid)
        )
