import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnalysisJobStatus,
    ConfidentialLevel,
    ErrorCode,
    PermissionLevel,
)
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.schemas.analysis import AnalysisJobCreate, AnalysisJobResponse, AnalysisPlan
from app.services.audit_service import AuditService
from app.services.llm_service import LLMService
from app.services.masking_service import MaskingService
from app.services.permission_service import PermissionService
from app.services.spreadsheet_analysis_service import (
    SpreadsheetAnalysisService,
    execute_spreadsheet_analysis,
)
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage
from app.workers.process_runner import (
    ProcessWorkerCancelledError,
    run_in_process,
)


class AnalysisJobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.spreadsheet_service = SpreadsheetAnalysisService()

    def create(self, payload: AnalysisJobCreate, principal: Principal) -> AnalysisJob:
        source = self.get_analysis_file(
            payload.file_id,
            principal,
            required=PermissionLevel.WRITE,
        )
        normalized, _warnings = self.spreadsheet_service.validate_plan(
            source.file_path,
            source.file_type,
            payload.plan,
        )
        user = AuditService(self.db).ensure_user(principal)
        session_id = payload.session_id
        if session_id is None and source.session_id is not None:
            source_session = self.db.get(ChatSession, source.session_id)
            if source_session is not None and (
                source_session.user_id == user.id or "admin" in principal.roles
            ):
                session_id = source.session_id
        if payload.session_id is not None:
            session = self._ensure_session_access(session_id, principal)
            if session.workspace_id != source.workspace_id:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "The selected file and chat session belong to different workspaces.",
                    400,
                )
        job = AnalysisJob(
            workspace_id=source.workspace_id,
            session_id=session_id,
            file_id=source.id,
            created_by=user.id,
            status=AnalysisJobStatus.QUEUED.value,
            progress=0,
            request_json=normalized.model_dump(mode="json"),
        )
        self.db.add(job)
        self.db.flush()
        AuditService(self.db).record_event(
            "analysis_job_queued",
            "A deterministic spreadsheet analysis job was queued.",
            {"file_id": str(source.id), "plan": job.request_json},
            user_id=user.id,
            target_type="analysis_job",
            target_id=job.id,
            risk_level="low",
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def claim_next(self) -> AnalysisJob | None:
        statement = (
            select(AnalysisJob)
            .where(AnalysisJob.status == AnalysisJobStatus.QUEUED.value)
            .order_by(AnalysisJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = self.db.scalar(statement)
        if job is None:
            return None
        job.status = AnalysisJobStatus.RUNNING.value
        job.progress = max(job.progress, 5)
        job.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        return job

    def get(
        self,
        job_id: UUID,
        principal: Principal,
        *,
        required: PermissionLevel = PermissionLevel.READ,
    ) -> AnalysisJob:
        job = self.db.get(AnalysisJob, job_id)
        if job is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis job not found.",
                404,
            )
        self.get_analysis_file(job.file_id, principal, required=required)
        return job

    def list_jobs(
        self,
        principal: Principal,
        *,
        session_id: UUID | None = None,
        workspace_id: UUID | None = None,
        file_id: UUID | None = None,
        status: AnalysisJobStatus | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AnalysisJob], int, UUID]:
        if session_id is None and workspace_id is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "session_id or workspace_id is required.",
                400,
            )
        workspace_service = WorkspaceService(self.db)
        if session_id is not None:
            session = self._ensure_session_access(session_id, principal)
            workspace = workspace_service.resolve_for_session(
                session,
                principal,
                workspace_id,
                required=PermissionLevel.READ,
            )
        else:
            workspace = workspace_service.get(workspace_id, principal)
        filters = [AnalysisJob.workspace_id == workspace.id]
        if file_id is not None:
            source = self.get_analysis_file(file_id, principal)
            if source.workspace_id != workspace.id:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Analysis file does not belong to the requested workspace.",
                    400,
                )
            filters.append(AnalysisJob.file_id == file_id)
        if status is not None:
            filters.append(AnalysisJob.status == status.value)
        total = self.db.scalar(select(func.count()).select_from(AnalysisJob).where(*filters)) or 0
        items = list(
            self.db.scalars(
                select(AnalysisJob)
                .where(*filters)
                .order_by(AnalysisJob.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return items, total, workspace.id

    def cancel(self, job_id: UUID, principal: Principal) -> AnalysisJob:
        job = self.get(job_id, principal, required=PermissionLevel.WRITE)
        if job.status == AnalysisJobStatus.CANCELLED.value:
            return job
        if job.status in {
            AnalysisJobStatus.COMPLETED.value,
            AnalysisJobStatus.FAILED.value,
        }:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Only queued or running analysis jobs can be cancelled.",
                409,
            )
        now = datetime.utcnow()
        job.status = AnalysisJobStatus.CANCELLED.value
        job.cancel_requested_at = now
        job.updated_at = now
        job.finished_at = now
        user = AuditService(self.db).ensure_user(principal)
        AuditService(self.db).record_event(
            "analysis_job_cancelled",
            "An analysis job cancellation was requested.",
            {"file_id": str(job.file_id), "progress": job.progress},
            user_id=user.id,
            target_type="analysis_job",
            target_id=job.id,
            risk_level="low",
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def retry(self, job_id: UUID, principal: Principal) -> AnalysisJob:
        original = self.get(
            job_id,
            principal,
            required=PermissionLevel.WRITE,
        )
        if original.status not in {
            AnalysisJobStatus.FAILED.value,
            AnalysisJobStatus.CANCELLED.value,
        }:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Only failed or cancelled analysis jobs can be retried.",
                409,
            )
        retry_job = self.create(
            AnalysisJobCreate(
                file_id=original.file_id,
                session_id=original.session_id,
                plan=AnalysisPlan.model_validate(original.request_json),
            ),
            principal,
        )
        retry_job.retry_of_job_id = original.id
        retry_job.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(retry_job)
        return retry_job

    def get_analysis_file(
        self,
        file_id: UUID,
        principal: Principal,
        *,
        required: PermissionLevel = PermissionLevel.READ,
    ) -> AnalysisFile:
        source = self.db.get(AnalysisFile, file_id)
        if source is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis file not found.",
                404,
            )
        if source.expires_at is not None and source.expires_at <= datetime.utcnow():
            job_ids = list(
                self.db.scalars(select(AnalysisJob.id).where(AnalysisJob.file_id == source.id))
            )
            self.db.execute(delete(AnalysisJob).where(AnalysisJob.file_id == source.id))
            self.db.delete(source)
            self.db.commit()
            storage = WorkspaceStorage()
            for job_id in job_ids:
                storage.delete_job_tree(source.workspace_id, job_id)
            storage.delete_file_tree(source.workspace_id, source.id)
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis file has expired.",
                410,
            )
        WorkspaceService(self.db).get(
            source.workspace_id,
            principal,
            required,
        )
        PermissionService(self.db).ensure_level_access(
            principal,
            ConfidentialLevel(source.confidential_level),
        )
        return source

    def _ensure_session_access(
        self,
        session_id: UUID,
        principal: Principal,
    ) -> ChatSession:
        session = self.db.get(ChatSession, session_id)
        if session is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Chat session not found.",
                404,
            )
        user = AuditService(self.db).ensure_user(principal)
        if session.user_id != user.id and "admin" not in principal.roles:
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to access this session.",
                403,
            )
        return session

    def response(self, job: AnalysisJob) -> AnalysisJobResponse:
        return AnalysisJobResponse(
            job_id=job.id,
            workspace_id=job.workspace_id,
            session_id=job.session_id,
            file_id=job.file_id,
            status=AnalysisJobStatus(job.status),
            plan=AnalysisPlan.model_validate(job.request_json),
            progress=job.progress,
            result=job.result_json,
            error_message=job.error_message,
            retry_of_job_id=job.retry_of_job_id,
            cancel_requested_at=job.cancel_requested_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )

    async def explain(self, job: AnalysisJob) -> str:
        if job.status != AnalysisJobStatus.COMPLETED.value or job.result_json is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Only completed analysis jobs can be explained.",
                409,
            )
        serialized = json.dumps(job.result_json, ensure_ascii=False)
        serialized = serialized[: settings.analysis_explanation_max_chars]
        system_prompt = (
            "你是公司內部資料分析結果解說助理。"
            "後端已完成所有統計運算。"
            "不可重新計算、修改或虛構任何數字；"
            "只能根據 result_json 說明主要發現。"
            "只有在資料明確支持時，才可指出最高、最低或異常；"
            "不可推論因果。"
            "請使用繁體中文，回答簡潔並保留原始欄位名稱。"
        )
        result_dlp = MaskingService(self.db).scan_and_mask(
            serialized,
            location="context",
        )
        if result_dlp.blocked:
            raise APIError(
                ErrorCode.DLP_BLOCKED,
                "The analysis result contains restricted information.",
                400,
            )
        user_prompt = f"result_json:\n{result_dlp.text}"
        answer = await LLMService().complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        response_dlp = MaskingService(self.db).scan_and_mask(
            answer,
            location="response",
        )
        return response_dlp.text


def run_analysis_job(job_id: UUID) -> None:
    """Execute one claimed job in a killable child process."""

    with SessionLocal() as db:
        job = db.get(AnalysisJob, job_id)
        if job is None or job.status not in {
            AnalysisJobStatus.QUEUED.value,
            AnalysisJobStatus.RUNNING.value,
        }:
            return
        if job.status == AnalysisJobStatus.QUEUED.value:
            job.status = AnalysisJobStatus.RUNNING.value
            job.progress = max(job.progress, 5)
            job.updated_at = datetime.utcnow()
            db.commit()
        try:
            source = db.get(AnalysisFile, job.file_id)
            if source is None:
                raise RuntimeError("Analysis source file was deleted.")
            job.progress = max(job.progress, 10)
            job.updated_at = datetime.utcnow()
            db.commit()
            result = run_in_process(
                execute_spreadsheet_analysis,
                timeout_seconds=settings.analysis_job_timeout_seconds,
                kwargs={
                    "file_path": source.file_path,
                    "file_type": source.file_type,
                    "plan_payload": job.request_json,
                },
                cancel_check=lambda: _analysis_job_cancelled(job_id),
            )
            db.refresh(job)
            if job.status == AnalysisJobStatus.CANCELLED.value:
                return
            job.result_json = result
            job.result_path = WorkspaceStorage().save_result(
                job.workspace_id,
                job.id,
                result,
            )
            job.status = AnalysisJobStatus.COMPLETED.value
            job.progress = 100
            job.error_message = None
        except ProcessWorkerCancelledError:
            db.refresh(job)
            job.status = AnalysisJobStatus.CANCELLED.value
            job.error_message = None
        except Exception as exc:
            db.refresh(job)
            if job.status != AnalysisJobStatus.CANCELLED.value:
                job.status = AnalysisJobStatus.FAILED.value
                job.progress = 100
                job.error_message = exc.message if isinstance(exc, APIError) else str(exc)
        job.updated_at = datetime.utcnow()
        job.finished_at = datetime.utcnow()
        db.commit()


def _analysis_job_cancelled(job_id: UUID) -> bool:
    with SessionLocal() as db:
        status = db.scalar(select(AnalysisJob.status).where(AnalysisJob.id == job_id))
    return status is None or status == AnalysisJobStatus.CANCELLED.value


def cleanup_expired_analysis_files() -> int:
    now = datetime.utcnow()
    with SessionLocal() as db:
        running_job = exists(
            select(AnalysisJob.id).where(
                AnalysisJob.file_id == AnalysisFile.id,
                AnalysisJob.status == AnalysisJobStatus.RUNNING.value,
            )
        )
        sources = list(
            db.scalars(
                select(AnalysisFile).where(
                    AnalysisFile.expires_at.is_not(None),
                    AnalysisFile.expires_at <= now,
                    ~running_job,
                )
            )
        )
        if not sources:
            return 0
        file_ids = [source.id for source in sources]
        workspace_file_ids = [(source.workspace_id, source.id) for source in sources]
        workspace_job_ids = list(
            db.execute(
                select(AnalysisJob.workspace_id, AnalysisJob.id).where(
                    AnalysisJob.file_id.in_(file_ids)
                )
            )
        )
        db.execute(delete(AnalysisJob).where(AnalysisJob.file_id.in_(file_ids)))
        db.execute(delete(AnalysisFile).where(AnalysisFile.id.in_(file_ids)))
        db.commit()
    storage = WorkspaceStorage()
    for workspace_id, job_id in workspace_job_ids:
        storage.delete_job_tree(workspace_id, job_id)
    for workspace_id, file_id in workspace_file_ids:
        storage.delete_file_tree(workspace_id, file_id)
    return len(workspace_file_ids)
