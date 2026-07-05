from pathlib import Path

from sqlalchemy.exc import ProgrammingError


def _migration_sql_path() -> Path:
    return Path(__file__).resolve().parent / "migrations" / "001_initial_schema.sql"


def init_db() -> None:
    from sqlalchemy import text

    from app.db.session import engine

    migration_path = _migration_sql_path()
    sql = migration_path.read_text(encoding="utf-8")

    try:
        with engine.begin() as connection:
            connection.execute(text(sql))
    except ProgrammingError as exc:
        if "permission denied to create extension" in str(exc).lower():
            admin_sql = Path(__file__).resolve().parent / "admin_extensions.sql"
            raise SystemExit(
                "The lm_agent database user cannot create PostgreSQL extensions. "
                f"Run {admin_sql} on the lm_agent database as a PostgreSQL superuser, "
                "then run python -m app.db.init_db again."
            ) from exc
        raise


def main() -> None:
    init_db()
    print(f"Initialized database schema from {_migration_sql_path()}")


if __name__ == "__main__":
    main()
