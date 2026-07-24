import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def _admin_sql_path() -> Path:
    return Path(__file__).resolve().parent / "admin_extensions.sql"


def init_extensions(database_url: str) -> None:
    sql = _admin_sql_path().read_text(encoding="utf-8")
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text(sql))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create PostgreSQL extensions required by lm_agent."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("ADMIN_DATABASE_URL", ""),
        help="Superuser PostgreSQL URL for the lm_agent database.",
    )
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit(
            "Missing admin database URL. Pass --database-url or set ADMIN_DATABASE_URL."
        )

    try:
        init_extensions(args.database_url)
    except SQLAlchemyError as exc:
        raise SystemExit(f"Failed to initialize extensions: {exc}") from exc

    print(f"Initialized PostgreSQL extensions from {_admin_sql_path()}")


if __name__ == "__main__":
    main()

