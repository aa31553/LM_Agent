import logging
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.base import Base
from app.db.init_db import init_db
from app.db.session import database_backend, engine
from app.models.analysis import AnalysisFile, AnalysisJob, AnalysisPlanDraft
from app.models.chat import ChatSession
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
    chat_session_columns = (
        {column["name"]: column for column in inspector.get_columns("chat_sessions")}
        if "chat_sessions" in tables
        else {}
    )
    draft_columns = (
        {column["name"]: column for column in inspector.get_columns("analysis_plan_drafts")}
        if "analysis_plan_drafts" in tables
        else {}
    )
    analysis_job_columns = (
        {column["name"]: column for column in inspector.get_columns("analysis_jobs")}
        if "analysis_jobs" in tables
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
        or "chat_type" not in chat_session_columns
        or not {
            "plan_origin",
            "recipe_id",
            "recipe_version",
            "compiler_version",
            "dataset_hashes_json",
            "result_schema_json",
            "result_hash",
            "error_code",
            "error_details_json",
            "execution_duration_ms",
        }.issubset(analysis_job_columns)
        or not {
            "raw_llm_json",
            "normalized_intent_json",
            "normalization_actions_json",
            "validation_errors_json",
            "repair_attempted",
            "recipe_id",
            "recipe_version",
            "compiler_version",
        }.issubset(draft_columns)
    )
    if needs_upgrade:
        init_db()


def _ensure_sqlite_schema() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _backfill_sqlite_workspaces()
    inspector = inspect(engine)
    tables_to_upgrade = (
        (Document.__table__, {"knowledge_base_id": True}),
        (DocumentChunk.__table__, {"knowledge_base_id": True}),
        (DocumentImage.__table__, {"knowledge_base_id": True}),
        (
            ChatSession.__table__,
            {"chat_type": False, "workspace_id": True},
        ),
        (
            AnalysisFile.__table__,
            {
                "workspace_id": False,
                "session_id": True,
                "profile_path": True,
                "dataset_manifest": True,
                "profile_progress": False,
                "profile_error": True,
                "profiled_at": True,
            },
        ),
        (
            AnalysisJob.__table__,
            {
                "workspace_id": False,
                "session_id": True,
                "result_path": True,
                "draft_id": True,
                "plan_origin": False,
                "recipe_id": True,
                "recipe_version": True,
                "compiler_version": True,
                "dataset_hashes_json": False,
                "result_schema_json": True,
                "result_hash": True,
                "error_code": True,
                "error_details_json": True,
                "execution_duration_ms": True,
            },
        ),
        (
            AnalysisPlanDraft.__table__,
            {
                "raw_llm_json": True,
                "normalized_intent_json": True,
                "normalization_actions_json": False,
                "validation_errors_json": False,
                "repair_attempted": False,
                "recipe_id": True,
                "recipe_version": True,
                "compiler_version": True,
            },
        ),
    )
    for table, requirements in tables_to_upgrade:
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        needs_rebuild = any(
            column_name not in columns
            or (
                must_be_nullable is not None
                and columns[column_name]["nullable"] != must_be_nullable
            )
            for column_name, must_be_nullable in requirements.items()
        )
        if needs_rebuild:
            _rebuild_sqlite_table(table)
            inspector = inspect(engine)


def _backfill_sqlite_workspaces() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    required = {"users", "chat_sessions", "analysis_files", "analysis_jobs", "workspaces"}
    if not required.issubset(tables):
        return
    with engine.begin() as connection:
        columns = {
            table_name: {column["name"] for column in inspect(connection).get_columns(table_name)}
            for table_name in ("chat_sessions", "analysis_files", "analysis_jobs")
        }
        additions = {
            "chat_sessions": (("workspace_id", "CHAR(32)"),),
            "analysis_files": (
                ("workspace_id", "CHAR(32)"),
                ("profile_path", "TEXT"),
                ("dataset_manifest", "JSON"),
                ("profile_progress", "INTEGER NOT NULL DEFAULT 0"),
                ("profile_error", "TEXT"),
                ("profiled_at", "DATETIME"),
            ),
            "analysis_jobs": (
                ("workspace_id", "CHAR(32)"),
                ("result_path", "TEXT"),
                ("draft_id", "CHAR(32)"),
            ),
        }
        for table_name, table_additions in additions.items():
            for column_name, column_type in table_additions:
                if column_name not in columns[table_name]:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'
                    )
        user_ids = [
            row[0]
            for row in connection.exec_driver_sql('SELECT DISTINCT "user_id" FROM "chat_sessions"')
            if row[0] is not None
        ]
        for user_id in user_ids:
            existing = connection.exec_driver_sql(
                'SELECT "id" FROM "workspaces" '
                'WHERE "owner_user_id" = ? AND "is_personal" = 1 LIMIT 1',
                (user_id,),
            ).first()
            if existing is None:
                workspace_id = uuid4().hex
                connection.exec_driver_sql(
                    'INSERT INTO "workspaces" ('
                    '"id", "owner_user_id", "name", "visibility", '
                    '"is_personal", "is_active", "created_at", "updated_at"'
                    ") VALUES (?, ?, ?, 'private', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (workspace_id, user_id, "Personal Workspace"),
                )
            else:
                workspace_id = existing[0]
            connection.exec_driver_sql(
                'UPDATE "chat_sessions" SET "workspace_id" = ? '
                'WHERE "user_id" = ? AND "workspace_id" IS NULL',
                (workspace_id, user_id),
            )
        connection.exec_driver_sql(
            'UPDATE "analysis_files" SET "workspace_id" = ('
            'SELECT "workspace_id" FROM "chat_sessions" '
            'WHERE "chat_sessions"."id" = "analysis_files"."session_id"'
            ') WHERE "workspace_id" IS NULL'
        )
        connection.exec_driver_sql(
            'UPDATE "analysis_files" SET "expires_at" = NULL WHERE "workspace_id" IS NOT NULL'
        )
        connection.exec_driver_sql(
            'UPDATE "analysis_jobs" SET "workspace_id" = ('
            'SELECT "workspace_id" FROM "analysis_files" '
            'WHERE "analysis_files"."id" = "analysis_jobs"."file_id"'
            ') WHERE "workspace_id" IS NULL'
        )


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
                connection.exec_driver_sql(f'ALTER TABLE "{table.name}" RENAME TO "{old_name}"')
                table.create(bind=connection)
                common_columns = [
                    column.name for column in table.columns if column.name in old_columns
                ]
                quoted = ", ".join(f'"{name}"' for name in common_columns)
                connection.exec_driver_sql(
                    f'INSERT INTO "{table.name}" ({quoted}) SELECT {quoted} FROM "{old_name}"'
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
        raise RuntimeError(
            "Database schema initialization failed; documents.session_id is missing."
        )
    chat_session_columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
    if "chat_type" not in chat_session_columns:
        raise RuntimeError(
            "Database schema initialization failed; chat_sessions.chat_type is missing."
        )
    if "workspace_id" not in chat_session_columns:
        raise RuntimeError(
            "Database schema initialization failed; chat_sessions.workspace_id is missing."
        )
    workspace_columns = {column["name"] for column in inspector.get_columns("workspaces")}
    if not {"owner_user_id", "visibility", "is_personal"}.issubset(workspace_columns):
        raise RuntimeError("Database schema initialization failed; workspace columns are missing.")
    for table_name, required_columns in {
        "analysis_files": {
            "workspace_id",
            "profile_path",
            "dataset_manifest",
            "profile_progress",
        },
        "analysis_jobs": {
            "workspace_id",
            "result_path",
            "draft_id",
            "plan_origin",
            "recipe_id",
            "recipe_version",
            "compiler_version",
            "dataset_hashes_json",
            "result_schema_json",
            "result_hash",
            "error_code",
            "error_details_json",
            "execution_duration_ms",
        },
        "analysis_plan_drafts": {
            "raw_llm_json",
            "normalized_intent_json",
            "normalization_actions_json",
            "validation_errors_json",
            "repair_attempted",
            "recipe_id",
            "recipe_version",
            "compiler_version",
        },
    }.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not required_columns.issubset(columns):
            raise RuntimeError(
                "Database schema initialization failed; "
                f"{table_name} workspace columns are missing."
            )
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
            "Database schema initialization failed; columns must be nullable: " + ", ".join(invalid)
        )
