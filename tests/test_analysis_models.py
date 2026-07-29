from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.analysis import AnalysisFile, AnalysisJob


def test_analysis_tables_compile_for_postgresql() -> None:
    file_sql = str(
        CreateTable(AnalysisFile.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    job_sql = str(
        CreateTable(AnalysisJob.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "analysis_files" in file_sql
    assert "analysis_jobs" in job_sql
    assert "file_id" in job_sql
