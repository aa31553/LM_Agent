from uuid import UUID

from app.services.document_ingestion_service import DocumentIngestionService
from app.workers.base import QueueWorker


class EmbeddingTasks(QueueWorker):
    job_type = "embedding"

    def enqueue_embeddings(self, document_id: UUID) -> object:
        return self.enqueue(document_id)

    async def process_embeddings(self, document_id: str | UUID) -> None:
        await self.process_document_id(UUID(str(document_id)))

    async def process_document_id(self, document_id: UUID) -> None:
        if self.processor is not None:
            await super().process_document_id(document_id)
            return
        with self.db_factory() as db:
            await DocumentIngestionService(db=db).reindex(document_id, request_id="worker-embedding")
