import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.models.document import Document, DocumentProcessingJob
from app.repositories.processing_job_repository import DocumentProcessingJobRepository


logger = logging.getLogger(__name__)


class ProcessingQueueService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = DocumentProcessingJobRepository(db)

    def enqueue(self, document_id: UUID, job_type: str) -> DocumentProcessingJob:
        if self.db.get(Document, document_id) is None:
            raise APIError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found.", 404)
        active_job = self.repository.get_active(document_id=document_id, job_type=job_type)
        if active_job is not None:
            logger.info(
                "processing_job_already_active",
                extra={"document_id": str(document_id), "job_id": str(active_job.id), "job_type": job_type},
            )
            return active_job
        job = DocumentProcessingJob(
            document_id=document_id,
            job_type=job_type,
            status="queued",
            progress=0,
        )
        self.repository.add(job)
        self.db.commit()
        self.db.refresh(job)
        logger.info(
            "processing_job_queued",
            extra={"document_id": str(document_id), "job_id": str(job.id), "job_type": job_type},
        )
        return job

    def claim_next(self, job_type: str | None = None) -> DocumentProcessingJob | None:
        job = self.repository.get_next_queued(job_type=job_type)
        if job is None:
            return None
        job.status = "running"
        job.progress = max(job.progress or 0, 1)
        job.started_at = datetime.utcnow()
        job.error_message = None
        self.db.commit()
        self.db.refresh(job)
        logger.info(
            "processing_job_claimed",
            extra={"document_id": str(job.document_id), "job_id": str(job.id), "job_type": job.job_type},
        )
        return job

    def mark_completed(self, job_id: UUID, progress: int = 100) -> DocumentProcessingJob:
        job = self._get_job(job_id)
        job.status = "completed"
        job.progress = progress
        job.error_message = None
        job.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        logger.info(
            "processing_job_completed",
            extra={"document_id": str(job.document_id), "job_id": str(job.id), "job_type": job.job_type},
        )
        return job

    def mark_failed(self, job_id: UUID, error_message: str) -> DocumentProcessingJob:
        job = self._get_job(job_id)
        job.status = "failed"
        job.progress = 100
        job.error_message = error_message
        job.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        logger.error(
            "processing_job_failed",
            extra={
                "document_id": str(job.document_id),
                "job_id": str(job.id),
                "job_type": job.job_type,
                "error": error_message,
            },
        )
        return job

    def count_by_status(self) -> dict[str, int]:
        return self.repository.count_by_status()

    def _get_job(self, job_id: UUID) -> DocumentProcessingJob:
        job = self.repository.get(job_id)
        if job is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Processing job not found.", 404)
        return job
