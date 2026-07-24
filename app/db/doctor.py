from collections.abc import Iterable

from app.core.config import settings


def _format_check(name: str, ok: bool, detail: str) -> str:
    status = "OK" if ok else "FAIL"
    return f"[{status}] {name}: {detail}"


def run_checks() -> Iterable[str]:
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.session import engine

    yield f"DATABASE_URL={settings.database_url}"

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            yield _format_check("postgres_connection", True, "connected")

            extensions = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp')"
                    )
                )
            }
            yield _format_check("pgvector_extension", "vector" in extensions, "vector installed")
            yield _format_check("pg_trgm_extension", "pg_trgm" in extensions, "pg_trgm installed")
            yield _format_check("uuid_ossp_extension", "uuid-ossp" in extensions, "uuid-ossp installed")
            if {"vector", "pg_trgm", "uuid-ossp"} - extensions:
                yield (
                    "Hint: run app/db/admin_extensions.sql on the lm_agent database as a "
                    "PostgreSQL superuser, then run python -m app.db.init_db again."
                )

            table_exists = connection.execute(
                text("SELECT to_regclass('public.documents')")
            ).scalar_one()
            yield _format_check("schema_documents_table", bool(table_exists), "documents table present")
            image_table_exists = connection.execute(
                text("SELECT to_regclass('public.document_images')")
            ).scalar_one()
            yield _format_check(
                "schema_document_images_table",
                bool(image_table_exists),
                "document_images table present",
            )
    except SQLAlchemyError as exc:
        yield _format_check("postgres_connection", False, str(exc))


def main() -> None:
    for line in run_checks():
        print(line)


if __name__ == "__main__":
    main()
