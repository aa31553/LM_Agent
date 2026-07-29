import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AnalysisJobStatus, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.schemas.analysis import AnalysisJobCreate, AnalysisJobResponse, AnalysisPlan
from app.services.audit_service import AuditService
from app.services.llm_service import LLMService
from app.services.masking_service import MaskingService
from app.services.spreadsheet_analysis_service import (
    SpreadsheetAnalysisService,
    execute_spreadsheet_analysis,
)
from app.storage.local_storage import LocalStorage
from app.workers.process_runner import run_in_process


class AnalysisJobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.spreadsheet_service = SpreadsheetAnalysisService()

    def create(self, payload: AnalysisJobCreate, principal: Principal) -> AnalysisJob:
        source = self.get_analysis_file(payload.file_id, principal)
        normalized, _warnings = self.spreadsheet_service.validate_plan(
            source.file_path,
            source.file_type,
            payload.plan,
        )
        user = AuditService(self.db).ensure_user(principal)
        job = AnalysisJob(
            session_id=source.session_id,
            file_id=source.id,
            created_by=user.id,
            status=AnalysisJobStatus.QUEUED.value,
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
        job.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        return job

    def get(self, job_id: UUID, principal: Principal) -> AnalysisJob:
        job = self.db.get(AnalysisJob, job_id)
        if job is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis job not found.",
                404,
            )
        self.get_analysis_file(job.file_id, principal)
        return job

    def get_analysis_file(
        self,
        file_id: UUID,
        principal: Principal,
    ) -> AnalysisFile:
        source = self.db.get(AnalysisFile, file_id)
        if source is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis file not found.",
                404,
            )
        if source.expires_at is not None and source.expires_at <= datetime.utcnow():
            file_path = source.file_path
            self.db.execute(
                delete(AnalysisJob).where(AnalysisJob.file_id == source.id)
            )
            self.db.delete(source)
            self.db.commit()
            LocalStorage().delete_artifact(file_path)
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis file has expired.",
                410,
            )
        session = self.db.get(ChatSession, source.session_id)
        user = AuditService(self.db).ensure_user(principal)
        if session is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis session not found.",
                404,
            )
        if session.user_id != user.id and "admin" not in principal.roles:
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to access this analysis file.",
                403,
            )
        return source

    def response(self, job: AnalysisJob) -> AnalysisJobResponse:
        return AnalysisJobResponse(
            job_id=job.id,
            session_id=job.session_id,
            file_id=job.file_id,
            status=AnalysisJobStatus(job.status),
            plan=AnalysisPlan.model_validate(job.request_json),
            result=job.result_json,
            error_message=job.error_message,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )

    async def explain(self, job: AnalysisJob) -> str:
        if (
            job.status != AnalysisJobStatus.COMPLETED.value
            or job.result_json is None
        ):
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
            job.updated_at = datetime.utcnow()
            db.commit()
        try:
            source = db.get(AnalysisFile, job.file_id)
            if source is None:
                raise RuntimeError("Analysis source file was deleted.")
            result = run_in_process(
                execute_spreadsheet_analysis,
                timeout_seconds=settings.analysis_job_timeout_seconds,
                kwargs={
                    "file_path": source.file_path,
                    "file_type": source.file_type,
                    "plan_payload": job.request_json,
                },
            )
            job.result_json = result
            job.status = AnalysisJobStatus.COMPLETED.value
            job.error_message = None
        except Exception as exc:
            job.status = AnalysisJobStatus.FAILED.value
            job.error_message = (
                exc.message if isinstance(exc, APIError) else str(exc)
            )
        job.updated_at = datetime.utcnow()
        job.finished_at = datetime.utcnow()
        db.commit()


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
        paths = [source.file_path for source in sources]
        db.execute(
            delete(AnalysisJob).where(AnalysisJob.file_id.in_(file_ids))
        )
        db.execute(
            delete(AnalysisFile).where(AnalysisFile.id.in_(file_ids))
        )
        db.commit()
    storage = LocalStorage()
    for path in paths:
        storage.delete_artifact(path)
    return len(paths)
