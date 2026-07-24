from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.constants import ConfidentialLevel, DocumentStatus
from app.db.session import SessionLocal
from app.main import create_app
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin", "X-Request-ID": "test-request"}


def _latest_ready_document_id() -> str | None:
    with SessionLocal() as db:
        row = (
            db.query(Document)
            .filter(Document.status == DocumentStatus.READY.value, Document.file_type == "pdf")
            .order_by(Document.created_at.desc())
            .first()
        )
        return str(row.id) if row is not None else None


def test_documents_list_detail_and_status_api_against_lm_agent_db() -> None:
    document_id = _latest_ready_document_id()
    if document_id is None:
        pytest.skip("lm_agent DB does not contain a ready document.")

    client = TestClient(create_app())
    list_response = client.get("/api/v1/documents", headers=_auth_headers())
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["total"] >= 1
    assert any(item["document_id"] == document_id for item in list_body["items"])

    detail_response = client.get(f"/api/v1/documents/{document_id}", headers=_auth_headers())
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["status"] == "ready"
    assert detail["chunk_count"] > 0
    assert detail["file_type"] == "pdf"

    status_response = client.get(
        f"/api/v1/documents/{document_id}/status",
        headers=_auth_headers(),
    )
    assert status_response.status_code == 200
    assert status_response.json()["progress"] == 100


def test_archive_document_api_updates_status() -> None:
    with SessionLocal() as db:
        kb = db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).first()
        if kb is None:
            pytest.skip("lm_agent DB does not contain a knowledge base.")
        dummy_path = Path("data/uploads/test-archive-placeholder.pdf")
        dummy_path.parent.mkdir(parents=True, exist_ok=True)
        dummy_path.write_bytes(b"%PDF-1.4\n% test placeholder\n")
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-archive-placeholder.pdf",
            original_filename="archive-placeholder.pdf",
            title="archive-placeholder",
            file_type="pdf",
            file_path=str(dummy_path),
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.UPLOADED.value,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        document_id = str(document.id)

    client = TestClient(create_app())
    response = client.post(f"/api/v1/documents/{document_id}/archive", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "archived"

    with SessionLocal() as db:
        archived = db.get(Document, UUID(document_id))
        assert archived is not None
        assert archived.status == DocumentStatus.ARCHIVED.value
