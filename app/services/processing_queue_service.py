from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.models.document import Document, DocumentProcessingJob
from app.repositories.processing_job_repository import DocumentProcessingJobRepository


class ProcessingQueueService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = DocumentProcessingJobRepository(db)

    def enqueue(self, document_id: UUID, job_type: str) -> DocumentProcessingJob:
        if self.db.get(Document, document_id) is None:
            raise APIError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found.", 404)
        job = DocumentProcessingJob(
            document_id=document_id,
            job_type=job_type,
            status="queued",
            progress=0,
        )
        self.repository.add(job)
        self.db.commit()
        self.db.refresh(job)
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
        return job

    def mark_completed(self, job_id: UUID, progress: int = 100) -> DocumentProcessingJob:
        job = self._get_job(job_id)
        job.status = "completed"
        job.progress = progress
        job.error_message = None
        job.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_failed(self, job_id: UUID, error_message: str) -> DocumentProcessingJob:
        job = self._get_job(job_id)
        job.status = "failed"
        job.progress = 100
        job.error_message = error_message
        job.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        return job

    def _get_job(self, job_id: UUID) -> DocumentProcessingJob:
        job = self.repository.get(job_id)
        if job is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Processing job not found.", 404)
        return job
