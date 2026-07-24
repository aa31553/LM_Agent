import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.constants import ConfidentialLevel
from app.db.session import SessionLocal
from app.models.knowledge_base import KnowledgeBase
from app.services.document_ingestion_service import DocumentIngestionService
from app.utils.file_utils import is_supported_upload


DEFAULT_KB_NAME = "DATA Template Knowledge Base"


def _get_or_create_knowledge_base(name: str) -> KnowledgeBase:
    with SessionLocal() as db:
        _ensure_schema_ready(db)
        knowledge_base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
        if knowledge_base is not None:
            return knowledge_base

        knowledge_base = KnowledgeBase(
            name=name,
            description="Knowledge base created from local DATA folder templates.",
            owner_department="local",
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
            is_active=True,
        )
        db.add(knowledge_base)
        db.commit()
        db.refresh(knowledge_base)
        return knowledge_base


def _ensure_schema_ready(db) -> None:
    extensions = {
        row[0]
        for row in db.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp')"
            )
        )
    }
    missing_extensions = {"vector", "pg_trgm", "uuid-ossp"} - extensions
    if missing_extensions:
        raise RuntimeError(
            "lm_agent database is missing required extensions: "
            + ", ".join(sorted(missing_extensions))
            + ". Run app/db/admin_extensions.sql as a PostgreSQL superuser."
        )

    has_schema = db.execute(text("SELECT to_regclass('public.knowledge_bases')")).scalar_one()
    if not has_schema:
        raise RuntimeError(
            "lm_agent schema has not been initialized. Run python -m app.db.init_db first."
        )


async def ingest_data_folder(data_dir: Path, knowledge_base_name: str) -> None:
    knowledge_base = _get_or_create_knowledge_base(knowledge_base_name)
    files = sorted(path for path in data_dir.iterdir() if path.is_file() and is_supported_upload(path.name))
    if not files:
        print(f"No supported files found under {data_dir}")
        return

    for path in files:
        with SessionLocal() as db:
            service = DocumentIngestionService(db=db)
            document = await service.ingest_file_path(
                file_path=path,
                knowledge_base_id=knowledge_base.id,
                confidential_level=ConfidentialLevel.INTERNAL,
                department="local",
                document_type="data_template",
            )
            print(
                f"{path.name}: document_id={document.id} "
                f"status={document.status} chunks={document.chunk_count} "
                f"ocr_required={document.ocr_required} error={document.error_message or ''}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest supported files from the local DATA folder.")
    parser.add_argument("--data-dir", default="DATA", help="Folder containing template documents.")
    parser.add_argument("--knowledge-base", default=DEFAULT_KB_NAME)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(f"DATA folder does not exist: {data_dir}")

    try:
        asyncio.run(ingest_data_folder(data_dir, args.knowledge_base))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    except SQLAlchemyError as exc:
        raise SystemExit(
            "Database operation failed. Ensure lm_agent schema exists and pgvector is enabled. "
            "If extension creation fails as lm_agent, run as a PostgreSQL superuser: "
            "CREATE EXTENSION IF NOT EXISTS vector; "
            "CREATE EXTENSION IF NOT EXISTS pg_trgm; "
            "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; "
            f"Original error: {exc}"
        ) from exc


if __name__ == "__main__":
    main()
