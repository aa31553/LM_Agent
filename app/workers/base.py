import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.document import DocumentProcessingJob
from app.services.processing_queue_service import ProcessingQueueService

Processor = Callable[[UUID], Awaitable[None] | None]
logger = logging.getLogger(__name__)


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

        error_message = ""
        for attempt in range(1, settings.worker_retry_attempts + 1):
            try:
                await asyncio.wait_for(
                    self.process_document_id(document_id),
                    timeout=settings.worker_job_timeout_seconds,
                )
                break
            except TimeoutError:
                error_message = (
                    f"Processing timed out after {settings.worker_job_timeout_seconds} seconds."
                )
            except Exception as exc:
                error_message = str(exc)

            logger.warning(
                "processing_job_attempt_failed",
                extra={
                    "document_id": str(document_id),
                    "job_id": str(job_id),
                    "job_type": self.job_type,
                    "attempt": attempt,
                    "max_attempts": settings.worker_retry_attempts,
                    "error": error_message,
                },
            )
            if attempt < settings.worker_retry_attempts:
                await asyncio.sleep(min(settings.worker_poll_interval_seconds, 1.0))
        else:
            with self.db_factory() as db:
                return ProcessingQueueService(db).mark_failed(job_id, error_message)

        with self.db_factory() as db:
            return ProcessingQueueService(db).mark_completed(job_id)

    async def process_document_id(self, document_id: UUID) -> None:
        if self.processor is None:
            raise NotImplementedError("Queue worker processor is not configured.")
        result = self.processor(document_id)
        if isawaitable(result):
            await result

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop_event = stop_event or asyncio.Event()
        logger.info("queue_worker_started", extra={"job_type": self.job_type})
        try:
            while not stop_event.is_set():
                processed = await self.process_next()
                if processed is None:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=settings.worker_poll_interval_seconds,
                        )
                    except TimeoutError:
                        pass
        finally:
            logger.info("queue_worker_stopped", extra={"job_type": self.job_type})


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda _signum, _frame: stop_event.set())
