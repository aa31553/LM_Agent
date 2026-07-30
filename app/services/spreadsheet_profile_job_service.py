from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AnalysisFileStatus, ErrorCode, PermissionLevel
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.session import SessionLocal
from app.models.analysis import AnalysisFile
from app.services.analysis_job_service import AnalysisJobService
from app.services.spreadsheet_ingestion_service import profile_spreadsheet_file
from app.workers.process_runner import run_in_process


class SpreadsheetProfileJobService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def claim_next(self) -> AnalysisFile | None:
        source = self.db.scalar(
            select(AnalysisFile)
            .where(AnalysisFile.status == AnalysisFileStatus.PROFILE_QUEUED.value)
            .order_by(AnalysisFile.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if source is None:
            return None
        source.status = AnalysisFileStatus.PROFILING.value
        source.profile_progress = 5
        source.profile_error = None
        self.db.commit()
        self.db.refresh(source)
        return source

    def retry(self, file_id: UUID, principal: Principal) -> AnalysisFile:
        source = AnalysisJobService(self.db).get_analysis_file(
            file_id,
            principal,
            required=PermissionLevel.WRITE,
        )
        if source.status not in {
            AnalysisFileStatus.FAILED.value,
            AnalysisFileStatus.READY.value,
        }:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Only failed or completed profiling can be queued again.",
                409,
            )
        source.status = AnalysisFileStatus.PROFILE_QUEUED.value
        source.profile_progress = 0
        source.profile_error = None
        source.dataset_manifest = None
        source.profiled_at = None
        self.db.commit()
        self.db.refresh(source)
        return source


def run_spreadsheet_profile(file_id: UUID) -> None:
    with SessionLocal() as db:
        source = db.get(AnalysisFile, file_id)
        if source is None or source.status not in {
            AnalysisFileStatus.PROFILE_QUEUED.value,
            AnalysisFileStatus.PROFILING.value,
        }:
            return
        source.status = AnalysisFileStatus.PROFILING.value
        source.profile_progress = max(source.profile_progress, 10)
        db.commit()
        try:
            result = run_in_process(
                profile_spreadsheet_file,
                timeout_seconds=settings.analysis_profile_timeout_seconds,
                kwargs={
                    "workspace_id": source.workspace_id,
                    "file_id": source.id,
                    "file_path": source.file_path,
                    "file_type": source.file_type,
                    "storage_root": settings.local_storage_root,
                },
            )
            db.refresh(source)
            source.profile_path = result["profile_path"]
            source.dataset_manifest = result["dataset_manifest"]
            source.status = AnalysisFileStatus.READY.value
            source.profile_progress = 100
            source.profile_error = None
            source.profiled_at = datetime.utcnow()
        except Exception as exc:
            db.refresh(source)
            source.status = AnalysisFileStatus.FAILED.value
            source.profile_progress = 100
            source.profile_error = exc.message if isinstance(exc, APIError) else str(exc)
        db.commit()
