from pathlib import Path

from sqlalchemy.exc import ProgrammingError


def _migration_sql_paths() -> list[Path]:
    migration_dir = Path(__file__).resolve().parent / "migrations"
    return sorted(migration_dir.glob("*.sql"))


def init_db() -> None:
    from sqlalchemy import text

    from app.db.session import engine

    try:
        with engine.begin() as connection:
            for migration_path in _migration_sql_paths():
                sql = migration_path.read_text(encoding="utf-8")
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
    applied = ", ".join(path.name for path in _migration_sql_paths())
    print(f"Initialized database schema from: {applied}")


if __name__ == "__main__":
    main()
