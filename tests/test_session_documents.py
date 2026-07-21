from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.session import create_database_engine, get_db
from app.main import create_app
from app.models.chat import ChatSession
from app.models.document import Document, DocumentProcessingJob
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.rag.retriever import HybridRetriever


@pytest.fixture
def session_document_client(tmp_path: Path, monkeypatch):
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "local_storage_root", str(tmp_path / "uploads"))

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), session_factory, tmp_path
    engine.dispose()


def _headers(token: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "session-doc-test"}


def _upload_session_document(client: TestClient, token: str = "admin"):
    return client.post(
        "/api/v1/documents/upload",
        headers=_headers(token),
        files={"file": ("temporary.png", b"not-yet-processed", "image/png")},
        data={"scope": "session", "confidential_level": "internal"},
    )


def test_session_upload_creates_isolated_session_without_knowledge_base(
    session_document_client,
) -> None:
    client, session_factory, _ = session_document_client

    response = _upload_session_document(client)

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "session"
    assert body["knowledge_base_id"] is None
    assert body["session_id"]
    with session_factory() as db:
        document = db.get(Document, UUID(body["document_id"]))
        assert document is not None
        assert str(document.session_id) == body["session_id"]
        assert document.knowledge_base_id is None


def test_session_upload_rejects_knowledge_base_id(session_document_client) -> None:
    client, _, _ = session_document_client
    response = client.post(
        "/api/v1/documents/upload",
        headers=_headers(),
        files={"file": ("temporary.png", b"content", "image/png")},
        data={
            "scope": "session",
            "knowledge_base_id": "00000000-0000-0000-0000-000000000001",
            "confidential_level": "internal",
        },
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REQUEST"


def test_delete_session_removes_documents_jobs_and_files(session_document_client) -> None:
    client, session_factory, _ = session_document_client
    uploaded = _upload_session_document(client).json()
    with session_factory() as db:
        document = db.get(Document, UUID(uploaded["document_id"]))
        assert document is not None
        original_path = Path(document.file_path)
        assert original_path.is_file()

    response = client.delete(
        f"/api/v1/chat/sessions/{uploaded['session_id']}",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": uploaded["session_id"],
        "deleted_documents": 1,
        "deleted_messages": 0,
        "deleted_files": 1,
        "status": "deleted",
    }
    assert not original_path.exists()
    with session_factory() as db:
        assert db.get(ChatSession, UUID(uploaded["session_id"])) is None
        assert db.get(Document, UUID(uploaded["document_id"])) is None
        assert db.scalar(select(DocumentProcessingJob)) is None


def test_user_cannot_delete_another_users_session(session_document_client) -> None:
    client, _, _ = session_document_client
    uploaded = _upload_session_document(client, "alice|engineering|internal|reader").json()

    response = client.delete(
        f"/api/v1/chat/sessions/{uploaded['session_id']}",
        headers=_headers("bob|engineering|internal|reader"),
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_retrieval_only_reads_documents_from_the_same_session(
    session_document_client,
    monkeypatch,
) -> None:
    _, session_factory, tmp_path = session_document_client
    with session_factory() as db:
        user = User(
            external_user_id="session-reader",
            username="session-reader",
            clearance_level="internal",
        )
        db.add(user)
        db.flush()
        selected_session = ChatSession(user_id=user.id, title="selected")
        other_session = ChatSession(user_id=user.id, title="other")
        db.add_all([selected_session, other_session])
        db.flush()
        selected_document = Document(
            session_id=selected_session.id,
            filename="selected.txt",
            original_filename="selected.txt",
            file_type="image",
            file_path=str(tmp_path / "selected.txt"),
            confidential_level="internal",
            status="ready",
        )
        other_document = Document(
            session_id=other_session.id,
            filename="other.txt",
            original_filename="other.txt",
            file_type="image",
            file_path=str(tmp_path / "other.txt"),
            confidential_level="internal",
            status="ready",
        )
        db.add_all([selected_document, other_document])
        db.flush()
        db.add_all(
            [
                DocumentChunk(
                    document_id=selected_document.id,
                    chunk_index=0,
                    content="selected session lithium content",
                    confidential_level="internal",
                    embedding=[1.0, 0.0],
                ),
                DocumentChunk(
                    document_id=other_document.id,
                    chunk_index=0,
                    content="other session lithium content",
                    confidential_level="internal",
                    embedding=[1.0, 0.0],
                ),
            ]
        )
        db.commit()

        retriever = HybridRetriever(db)

        async def fake_embed(_texts):
            return [[1.0, 0.0]]

        monkeypatch.setattr(retriever.embedding_service, "embed_texts", fake_embed)
        results = await retriever.retrieve(
            query="lithium",
            knowledge_base_ids=[],
            session_id=selected_session.id,
            top_k=8,
            use_rerank=False,
        )

    assert len(results) == 1
    assert results[0].document_id == selected_document.id
    assert results[0].metadata["scope"] == "session"
