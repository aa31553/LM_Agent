from collections.abc import Awaitable, Callable
from inspect import isawaitable
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.models.document import DocumentProcessingJob
from app.services.processing_queue_service import ProcessingQueueService

Processor = Callable[[UUID], Awaitable[None] | None]


class QueueWorker:
    job_type: str

    def __init__(
        self,
        db_factory: sessionmaker[Session] = SessionLocal,
        processor: Processor | None = None,
    ) -> None:
        self.db_factory = db_factory
        self.processor = processor

    def enqueue(self, document_id: UUID) -> DocumentProcessingJob:
        with self.db_factory() as db:
            return ProcessingQueueService(db).enqueue(document_id, self.job_type)

    async def process_next(self) -> DocumentProcessingJob | None:
        with self.db_factory() as db:
            job = ProcessingQueueService(db).claim_next(self.job_type)
            if job is None:
                return None
            job_id = job.id
            document_id = job.document_id

        try:
            await self.process_document_id(document_id)
        except Exception as exc:
            with self.db_factory() as db:
                return ProcessingQueueService(db).mark_failed(job_id, str(exc))

        with self.db_factory() as db:
            return ProcessingQueueService(db).mark_completed(job_id)

    async def process_document_id(self, document_id: UUID) -> None:
        if self.processor is None:
            raise NotImplementedError("Queue worker processor is not configured.")
        result = self.processor(document_id)
        if isawaitable(result):
            await result
