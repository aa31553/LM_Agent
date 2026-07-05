from uuid import UUID

from app.models.document import DocumentProcessingJob
from app.repositories.base import BaseRepository


class DocumentProcessingJobRepository(BaseRepository[DocumentProcessingJob]):
    model = DocumentProcessingJob

    def list_recent(
        self,
        *,
        document_id: UUID | None = None,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[DocumentProcessingJob]:
        query = self.db.query(DocumentProcessingJob)
        if document_id is not None:
            query = query.filter(DocumentProcessingJob.document_id == document_id)
        if job_type is not None:
            query = query.filter(DocumentProcessingJob.job_type == job_type)
        if status is not None:
            query = query.filter(DocumentProcessingJob.status == status)
        return (
            query.order_by(DocumentProcessingJob.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_next_queued(self, *, job_type: str | None = None) -> DocumentProcessingJob | None:
        query = self.db.query(DocumentProcessingJob).filter(DocumentProcessingJob.status == "queued")
        if job_type is not None:
            query = query.filter(DocumentProcessingJob.job_type == job_type)
        return (
            query.order_by(DocumentProcessingJob.created_at.asc())
            .limit(1)
            .one_or_none()
        )
