from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.analysis import AnalysisFile, AnalysisJob, AnalysisPlanDraft
from app.models.workspace import AnalysisArtifact, Workspace, WorkspacePermission


def test_analysis_tables_compile_for_postgresql() -> None:
    file_sql = str(CreateTable(AnalysisFile.__table__).compile(dialect=postgresql.dialect()))
    job_sql = str(CreateTable(AnalysisJob.__table__).compile(dialect=postgresql.dialect()))
    assert "analysis_files" in file_sql
    assert "analysis_jobs" in job_sql
    assert "workspace_id" in file_sql
    assert "profile_path" in file_sql
    assert "dataset_manifest" in file_sql
    assert "profile_progress" in file_sql
    assert "workspace_id" in job_sql
    assert "result_path" in job_sql
    assert "draft_id" in job_sql
    assert "file_id" in job_sql
    assert "progress" in job_sql
    assert "retry_of_job_id" in job_sql


def test_workspace_phase2_migration_is_additive() -> None:
    migration = Path("app/db/migrations/999_session_analysis_workspace_phase2.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE IF NOT EXISTS workspaces" in migration
    assert "CREATE TABLE IF NOT EXISTS workspace_permissions" in migration
    assert "CREATE TABLE IF NOT EXISTS analysis_artifacts" in migration
    assert "ALTER COLUMN session_id DROP NOT NULL" in migration
    assert "ON DELETE SET NULL" in migration
    assert Workspace.__tablename__ == "workspaces"
    assert WorkspacePermission.__tablename__ == "workspace_permissions"
    assert AnalysisArtifact.__tablename__ == "analysis_artifacts"
    assert AnalysisPlanDraft.__tablename__ == "analysis_plan_drafts"


def test_workspace_phase3_4_migration_is_additive() -> None:
    migration = Path("app/db/migrations/999_session_analysis_workspace_phase3_4.sql").read_text(
        encoding="utf-8"
    )
    assert "ADD COLUMN IF NOT EXISTS dataset_manifest" in migration
    assert "CREATE TABLE IF NOT EXISTS analysis_plan_drafts" in migration
    assert "ADD COLUMN IF NOT EXISTS draft_id" in migration
