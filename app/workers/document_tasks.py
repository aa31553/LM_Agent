from uuid import UUID

from app.services.document_ingestion_service import DocumentIngestionService
from app.workers.base import QueueWorker


class DocumentTasks(QueueWorker):
    job_type = "document"

    def enqueue_document(self, document_id: UUID) -> object:
        return self.enqueue(document_id)

    async def process_document(self, document_id: str | UUID) -> None:
        await self.process_document_id(UUID(str(document_id)))

    async def process_document_id(self, document_id: UUID) -> None:
        if self.processor is not None:
            await super().process_document_id(document_id)
            return
        with self.db_factory() as db:
            await DocumentIngestionService(db=db).reindex(document_id, request_id="worker-document")
