import json
import time
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnalysisFileStatus,
    AnalysisJobStatus,
    ConfidentialLevel,
    ErrorCode,
    PermissionLevel,
    ThinkingMode,
)
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.schemas.analysis import AnalysisJobCreate, AnalysisJobResponse, AnalysisPlan
from app.services.audit_service import AuditService
from app.services.dataset_query_service import (
    DatasetQueryService,
    execute_dataset_analysis,
)
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
    ProcessWorkerTimeoutError,
    run_in_process,
)


class AnalysisJobService:
    def __init__(
        self,
        db: Session,
        spreadsheet_service: SpreadsheetAnalysisService | None = None,
    ) -> None:
        self.db = db
        self.spreadsheet_service = spreadsheet_service or SpreadsheetAnalysisService()

    def create(
        self,
        payload: AnalysisJobCreate,
        principal: Principal,
        *,
        draft_id: UUID | None = None,
    ) -> AnalysisJob:
        source = self.get_analysis_file(
            payload.file_id,
            principal,
            required=PermissionLevel.WRITE,
        )
        if source.status != AnalysisFileStatus.READY.value:
            raise APIError(
                ErrorCode.DOCUMENT_NOT_READY,
                "Spreadsheet preprocessing is not complete.",
                409,
                details={"file_id": str(source.id), "status": source.status},
            )
        if payload.plan.sources:
            if source.id not in {item.file_id for item in payload.plan.sources}:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "file_id must be included in AnalysisPlan.sources.",
                    400,
                )
            plan_sources = [
                self.get_analysis_file(
                    item.file_id,
                    principal,
                    required=PermissionLevel.READ,
                )
                for item in payload.plan.sources
            ]
            if any(item.workspace_id != source.workspace_id for item in plan_sources):
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "All analysis sources must belong to the same workspace.",
                    400,
                )
            not_ready = [
                str(item.id)
                for item in plan_sources
                if item.status != AnalysisFileStatus.READY.value or not item.dataset_manifest
            ]
            if not_ready:
                raise APIError(
                    ErrorCode.DOCUMENT_NOT_READY,
                    "All analysis sources must finish preprocessing.",
                    409,
                    details={"file_ids": not_ready},
                )
            manifests = {str(item.id): item.dataset_manifest for item in plan_sources}
            normalized, _warnings = DatasetQueryService().validate_plan(
                payload.plan,
                manifests,
            )
        else:
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
            draft_id=draft_id,
            plan_origin=normalized.plan_origin,
            recipe_id=normalized.recipe_id,
            recipe_version=normalized.recipe_version,
            compiler_version=normalized.compiler_version,
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
        job.error_code = "ANALYSIS_CANCELLED"
        job.error_details_json = {}
        if job.execution_duration_ms is None:
            job.execution_duration_ms = 0
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
            draft_id=original.draft_id,
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
            draft_id=job.draft_id,
            plan_origin=job.plan_origin,
            recipe_id=job.recipe_id,
            recipe_version=job.recipe_version,
            compiler_version=job.compiler_version,
            result_hash=job.result_hash,
            error_code=job.error_code,
            error_details=job.error_details_json,
            execution_duration_ms=job.execution_duration_ms,
            cancel_requested_at=job.cancel_requested_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )

    async def explain(
        self,
        job: AnalysisJob,
        *,
        model: str | None = None,
        thinking_mode: ThinkingMode = ThinkingMode.DEFAULT,
    ) -> str:
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
        answer = await LLMService(
            model=model,
            thinking_mode=thinking_mode,
        ).complete(
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
        started_at = time.monotonic()
        try:
            source = db.get(AnalysisFile, job.file_id)
            if source is None:
                raise RuntimeError("Analysis source file was deleted.")
            job.progress = max(job.progress, 10)
            job.updated_at = datetime.utcnow()
            db.commit()
            plan = AnalysisPlan.model_validate(job.request_json)
            if plan.sources:
                source_ids = [item.file_id for item in plan.sources]
                analysis_sources = list(
                    db.scalars(select(AnalysisFile).where(AnalysisFile.id.in_(source_ids)))
                )
                manifests = {str(item.id): item.dataset_manifest for item in analysis_sources}
                if len(manifests) != len(set(source_ids)) or any(
                    not manifest for manifest in manifests.values()
                ):
                    raise RuntimeError("One or more preprocessed datasets are unavailable.")
                worker = execute_dataset_analysis
                worker_kwargs = {
                    "plan_payload": job.request_json,
                    "manifests": manifests,
                }
            else:
                worker = execute_spreadsheet_analysis
                worker_kwargs = {
                    "file_path": source.file_path,
                    "file_type": source.file_type,
                    "plan_payload": job.request_json,
                }
            result = run_in_process(
                worker,
                timeout_seconds=settings.analysis_job_timeout_seconds,
                kwargs=worker_kwargs,
                cancel_check=lambda: _analysis_job_cancelled(job_id),
            )
            source_file_ids = (
                [str(item.file_id) for item in plan.sources] if plan.sources else [str(source.id)]
            )
            for chart in result.get("charts", []):
                chart["source"] = {
                    "job_id": str(job.id),
                    "file_ids": source_file_ids,
                    "x_field": chart.get("x_field"),
                    "y_field": chart.get("y_field"),
                    "series_field": chart.get("series_field"),
                }
            db.refresh(job)
            if job.status == AnalysisJobStatus.CANCELLED.value:
                job.execution_duration_ms = max(
                    0,
                    round((time.monotonic() - started_at) * 1000),
                )
                db.commit()
                return
            job.result_json = result
            job.dataset_hashes_json = result.get("lineage", {}).get(
                "dataset_hashes",
                [],
            )
            job.result_schema_json = result.get("schema")
            job.result_hash = result.get("lineage", {}).get("result_hash")
            job.result_path = WorkspaceStorage().save_result(
                job.workspace_id,
                job.id,
                result,
            )
            job.status = AnalysisJobStatus.COMPLETED.value
            job.progress = 100
            job.error_message = None
            job.error_code = None
            job.error_details_json = None
            if plan.recipe_id is not None:
                audit = AuditService(db)
                metadata = {
                    "recipe_id": plan.recipe_id,
                    "recipe_version": plan.recipe_version,
                    "compiler_version": plan.compiler_version,
                    "result_hash": job.result_hash,
                    "warning_count": len(result.get("summary", {}).get("warnings", [])),
                }
                audit.record_event(
                    "analysis_result_validated",
                    "Recipe output matched its declared result contract.",
                    metadata,
                    user_id=job.created_by,
                    target_type="analysis_job",
                    target_id=job.id,
                    risk_level="low",
                )
                audit.record_event(
                    "analysis_chart_built",
                    "Trusted chart schemas were built from validated recipe results.",
                    {
                        **metadata,
                        "chart_count": len(result.get("charts", [])),
                    },
                    user_id=job.created_by,
                    target_type="analysis_job",
                    target_id=job.id,
                    risk_level="low",
                )
        except ProcessWorkerCancelledError:
            db.refresh(job)
            job.status = AnalysisJobStatus.CANCELLED.value
            job.error_message = None
            job.error_code = "ANALYSIS_CANCELLED"
            job.error_details_json = {}
        except Exception as exc:
            db.refresh(job)
            if job.status != AnalysisJobStatus.CANCELLED.value:
                job.status = AnalysisJobStatus.FAILED.value
                job.progress = 100
                job.error_message = exc.message if isinstance(exc, APIError) else str(exc)
                job.error_code, job.error_details_json = _structured_analysis_error(exc)
                if job.recipe_id is not None:
                    AuditService(db).record_event(
                        "analysis_recipe_failed",
                        "A deterministic analysis recipe failed.",
                        {
                            "recipe_id": job.recipe_id,
                            "recipe_version": job.recipe_version,
                            "error_code": (
                                job.error_code
                            ),
                        },
                        user_id=job.created_by,
                        target_type="analysis_job",
                        target_id=job.id,
                        risk_level="medium",
                    )
        job.execution_duration_ms = max(
            0,
            round((time.monotonic() - started_at) * 1000),
        )
        job.updated_at = datetime.utcnow()
        job.finished_at = datetime.utcnow()
        db.commit()


def _structured_analysis_error(exc: Exception) -> tuple[str, dict]:
    if isinstance(exc, APIError):
        return exc.error_code.value, dict(exc.details)
    if isinstance(exc, ProcessWorkerTimeoutError):
        return "ANALYSIS_TIMEOUT", {"exception_type": type(exc).__name__}
    return type(exc).__name__, {"exception_type": type(exc).__name__}


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
