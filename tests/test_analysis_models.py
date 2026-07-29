import importlib.util
from pathlib import Path

from sqlalchemy import Column, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base


def _load_analysis_module():
    path = Path(__file__).parents[1] / "app" / "models" / "analysis.py"
    spec = importlib.util.spec_from_file_location(
        "analysis_model_under_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_analysis_tables_compile_for_postgresql() -> None:
    module = _load_analysis_module()
    metadata = Base.metadata
    Table(
        "chat_sessions",
        metadata,
        Column("id", postgresql.UUID, primary_key=True),
        extend_existing=True,
    )
    Table(
        "users",
        metadata,
        Column("id", postgresql.UUID, primary_key=True),
        extend_existing=True,
    )
    file_sql = str(
        CreateTable(module.AnalysisFile.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    job_sql = str(
        CreateTable(module.AnalysisJob.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "analysis_files" in file_sql
    assert "analysis_jobs" in job_sql
    assert "file_id" in job_sql
