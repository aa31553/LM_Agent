from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.constants import ConfidentialLevel
from app.core.security import Principal
from app.db.base import Base
from app.db.session import create_database_engine, get_db
from app.integrations.openai_compatible_client import ChatCompletionResult
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.chat import ChatSession
from app.models.document import Document, DocumentProcessingJob
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.rag.retriever import HybridRetriever
from app.schemas.chat import ChatQueryRequest
from app.services.rag_service import RAGService


class FailIfCalledRetriever:
    async def retrieve(self, **kwargs):
        raise AssertionError("Retrieval must be skipped when no document source is available.")


class GeneralKnowledgeLLM:
    answer = "未檢索到相關文獻。以下依通用知識回答：我是內部智慧助理。"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete_messages(self, messages, tools=None, tool_choice=None):
        self.requests.append(messages)
        assert "no literature retrieval was performed" in messages[0]["content"]
        assert "General knowledge only" in messages[-1]["content"]
        assert "Context:" not in messages[-1]["content"]
        return ChatCompletionResult(content=self.answer, tool_calls=[])

    async def stream_complete_messages(self, messages):
        self.requests.append(messages)
        assert "no literature retrieval was performed" in messages[0]["content"]
        assert "General knowledge only" in messages[-1]["content"]
        assert "Context:" not in messages[-1]["content"]
        yield self.answer[:12]
        yield self.answer[12:]


class TransactionCheckingLLM(GeneralKnowledgeLLM):
    def __init__(self, db: Session) -> None:
        super().__init__()
        self.db = db

    async def complete_messages(self, messages, tools=None, tool_choice=None):
        assert self.db.in_transaction() is False
        return await super().complete_messages(messages, tools=tools, tool_choice=tool_choice)


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


def _principal() -> Principal:
    return Principal(
        external_user_id=f"general-mode-{uuid4()}",
        username="general-mode",
        department="qa",
        roles={"employee"},
        clearance_level=ConfidentialLevel.INTERNAL,
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
        "deleted_analysis_files": 0,
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
async def test_query_without_document_scope_uses_general_knowledge_mode(
    session_document_client,
) -> None:
    _, session_factory, _ = session_document_client
    with session_factory() as db:
        service = RAGService(db)
        service.retriever = FailIfCalledRetriever()
        service.llm_service = GeneralKnowledgeLLM()

        response = await service.answer(
            ChatQueryRequest(
                session_id=uuid4(),
                knowledge_base_ids=[],
                query="你好，請自我介紹",
                top_k=8,
                use_rerank=True,
                use_masking=True,
                use_tools=False,
            ),
            request_id="general-query-test",
            principal=_principal(),
        )

        assert response.answer == GeneralKnowledgeLLM.answer
        assert response.citations == []
        assert response.images == []
        audit_event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "query_executed",
                AuditEvent.target_id == response.message_id,
            )
        )
        assert audit_event is not None
        assert audit_event.event_metadata["answer_mode"] == "general_knowledge"
        assert audit_event.event_metadata["retrieval_skipped"] is True
        assert audit_event.event_metadata["session_documents_available"] is False


@pytest.mark.asyncio
async def test_query_releases_database_transaction_before_llm_call(
    session_document_client,
) -> None:
    _, session_factory, _ = session_document_client
    with session_factory() as db:
        service = RAGService(db)
        service.retriever = FailIfCalledRetriever()
        service.llm_service = TransactionCheckingLLM(db)

        response = await service.answer(
            ChatQueryRequest(
                knowledge_base_ids=[],
                query="請確認 LLM 呼叫前不持有資料庫 transaction",
            ),
            request_id="transaction-boundary-test",
            principal=_principal(),
        )

        assert response.answer == GeneralKnowledgeLLM.answer


@pytest.mark.asyncio
async def test_stream_without_document_scope_uses_general_knowledge_mode(
    session_document_client,
) -> None:
    _, session_factory, _ = session_document_client
    with session_factory() as db:
        service = RAGService(db)
        service.retriever = FailIfCalledRetriever()
        service.llm_service = GeneralKnowledgeLLM()

        events = [
            event
            async for event in service.stream_answer(
                ChatQueryRequest(
                    session_id=uuid4(),
                    knowledge_base_ids=[],
                    query="你好，請自我介紹",
                    use_tools=False,
                ),
                request_id="general-stream-test",
                principal=_principal(),
            )
        ]

        assert [event["event"] for event in events] == ["start", "delta", "delta", "done"]
        assert events[-1]["response"].answer == GeneralKnowledgeLLM.answer
        assert events[-1]["response"].citations == []


@pytest.mark.asyncio
async def test_stream_includes_prior_messages_from_the_same_session(
    session_document_client,
) -> None:
    _, session_factory, _ = session_document_client
    with session_factory() as db:
        service = RAGService(db)
        service.retriever = FailIfCalledRetriever()
        llm = GeneralKnowledgeLLM()
        service.llm_service = llm
        principal = _principal()
        session_id = uuid4()
        first = await service.answer(
            ChatQueryRequest(
                session_id=session_id,
                knowledge_base_ids=[],
                query="Remember that the release name is Aurora.",
                use_tools=False,
            ),
            request_id="history-query-test",
            principal=principal,
        )

        events = [
            event
            async for event in service.stream_answer(
                ChatQueryRequest(
                    session_id=first.session_id,
                    knowledge_base_ids=[],
                    query="What release name did I mention?",
                    use_tools=False,
                ),
                request_id="history-stream-test",
                principal=principal,
            )
        ]

        assert events[-1]["event"] == "done"
        assert [item["role"] for item in llm.requests[-1]] == [
            "system",
            "user",
            "assistant",
            "user",
        ]
        assert "Aurora" in llm.requests[-1][1]["content"]
        assert "What release name" in llm.requests[-1][-1]["content"]


def test_session_document_presence_includes_documents_not_ready_for_retrieval(
    session_document_client,
) -> None:
    _, session_factory, tmp_path = session_document_client
    with session_factory() as db:
        user = User(
            external_user_id="pending-document-owner",
            username="pending-document-owner",
            clearance_level="internal",
        )
        db.add(user)
        db.flush()
        chat_session = ChatSession(user_id=user.id, title="pending document")
        db.add(chat_session)
        db.flush()
        db.add(
            Document(
                session_id=chat_session.id,
                filename="pending.md",
                original_filename="pending.md",
                file_type="markdown",
                file_path=str(tmp_path / "pending.md"),
                confidential_level="internal",
                status="uploaded",
            )
        )
        db.flush()

        assert RAGService(db)._session_has_documents(chat_session.id) is True


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
