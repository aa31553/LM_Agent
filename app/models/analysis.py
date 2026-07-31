from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalysisFile(Base):
    __tablename__ = "analysis_files"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    profile_path: Mapped[str | None] = mapped_column(Text)
    dataset_manifest: Mapped[dict | None] = mapped_column(JSON)
    profile_progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile_error: Mapped[str | None] = mapped_column(Text)
    profiled_at: Mapped[datetime | None] = mapped_column(DateTime)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confidential_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), index=True
    )
    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    result_path: Mapped[str | None] = mapped_column(Text)
    plan_origin: Mapped[str] = mapped_column(String(32), nullable=False, default="direct")
    recipe_id: Mapped[str | None] = mapped_column(String(64), index=True)
    recipe_version: Mapped[str | None] = mapped_column(String(16))
    compiler_version: Mapped[str | None] = mapped_column(String(16))
    dataset_hashes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_schema_json: Mapped[dict | None] = mapped_column(JSON)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64), index=True)
    error_details_json: Mapped[dict | None] = mapped_column(JSON)
    execution_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    retry_of_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("analysis_plan_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class AnalysisPlanDraft(Base):
    __tablename__ = "analysis_plan_drafts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_llm_json: Mapped[dict | None] = mapped_column(JSON)
    normalized_intent_json: Mapped[dict | None] = mapped_column(JSON)
    normalization_actions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    validation_errors_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    repair_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recipe_id: Mapped[str | None] = mapped_column(String(64), index=True)
    recipe_version: Mapped[str | None] = mapped_column(String(16))
    compiler_version: Mapped[str | None] = mapped_column(String(16))
    warnings_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    clarification_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="validated")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
