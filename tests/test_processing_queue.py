from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.constants import ConfidentialLevel, DocumentStatus
from app.db.session import SessionLocal
from app.main import create_app
from app.models.document import Document, DocumentProcessingJob
from app.models.knowledge_base import KnowledgeBase
from app.workers.document_tasks import DocumentTasks
from app.workers.embedding_tasks import EmbeddingTasks
from app.workers.ocr_tasks import OCRTasks


def _create_queue_test_document(file_path: str = "data/uploads/queue-test.pdf") -> tuple[UUID, UUID]:
    with SessionLocal() as db:
        kb = KnowledgeBase(
            name=f"queue-test-{uuid4()}",
            description="queue worker test",
            owner_department="qa",
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add(kb)
        db.flush()
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-queue-test.pdf",
            original_filename="queue-test.pdf",
            title="queue-test",
            file_type="pdf",
            file_path=file_path,
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.UPLOADED.value,
            chunk_count=0,
        )
        db.add(document)
        db.commit()
        return document.id, kb.id


def _cleanup_queue_test_document(document_id: UUID, kb_id: UUID) -> None:
    with SessionLocal() as db:
        db.execute(delete(DocumentProcessingJob).where(DocumentProcessingJob.document_id == document_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        db.commit()


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin", "X-Request-ID": "queue-test"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worker_cls", "enqueue_method", "expected_type"),
    [
        (DocumentTasks, "enqueue_document", "document"),
        (OCRTasks, "enqueue_ocr", "ocr"),
        (EmbeddingTasks, "enqueue_embeddings", "embedding"),
    ],
)
async def test_queue_workers_process_queued_jobs(worker_cls, enqueue_method, expected_type) -> None:
    document_id, kb_id = _create_queue_test_document()
    processed: list[UUID] = []

    async def processor(processed_document_id: UUID) -> None:
        processed.append(processed_document_id)

    try:
        worker = worker_cls(processor=processor)
        job = getattr(worker, enqueue_method)(document_id)
        assert job.job_type == expected_type
        assert job.status == "queued"

        completed = await worker.process_next()
        assert completed is not None
        assert completed.id == job.id
        assert completed.status == "completed"
        assert completed.progress == 100
        assert completed.started_at is not None
        assert completed.finished_at is not None
        assert processed == [document_id]

        assert await worker.process_next() is None
    finally:
        _cleanup_queue_test_document(document_id, kb_id)


@pytest.mark.asyncio
async def test_queue_worker_marks_failed_job() -> None:
    document_id, kb_id = _create_queue_test_document()

    async def processor(processed_document_id: UUID) -> None:
        assert processed_document_id == document_id
        raise RuntimeError("planned worker failure")

    try:
        worker = EmbeddingTasks(processor=processor)
        job = worker.enqueue_embeddings(document_id)

        failed = await worker.process_next()
        assert failed is not None
        assert failed.id == job.id
        assert failed.status == "failed"
        assert failed.progress == 100
        assert failed.error_message == "planned worker failure"
        assert failed.started_at is not None
        assert failed.finished_at is not None
    finally:
        _cleanup_queue_test_document(document_id, kb_id)


def test_reindex_api_queues_document_job(tmp_path) -> None:
    source = tmp_path / "queue-reindex.pdf"
    source.write_bytes(b"%PDF-1.4\n%queue test\n")
    document_id, kb_id = _create_queue_test_document(str(source))
    try:
        response = TestClient(create_app()).post(
            f"/api/v1/documents/{document_id}/reindex",
            headers=_admin_headers(),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        assert response.json()["message"] == "Reindex task has been queued."

        with SessionLocal() as db:
            job = (
                db.query(DocumentProcessingJob)
                .filter(
                    DocumentProcessingJob.document_id == document_id,
                    DocumentProcessingJob.job_type == "document",
                    DocumentProcessingJob.status == "queued",
                )
                .one()
            )
            assert job.progress == 0
    finally:
        _cleanup_queue_test_document(document_id, kb_id)
