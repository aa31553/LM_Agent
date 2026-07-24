import asyncio
from uuid import UUID

from app.core.logging import configure_logging
from app.services.document_ingestion_service import DocumentIngestionService
from app.workers.base import QueueWorker, install_signal_handlers


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
            await DocumentIngestionService(db=db).process_document(
                document_id,
                request_id="worker-document",
            )


async def run_worker() -> None:
    configure_logging()
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)
    await DocumentTasks().run_forever(stop_event)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
