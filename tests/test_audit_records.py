from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.constants import ConfidentialLevel, DocumentStatus
from app.core.security import Principal
from app.db.session import SessionLocal
from app.main import create_app
from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_image import DocumentImage
from app.models.knowledge_base import KnowledgeBase
from app.models.masking import MaskingEvent
from app.models.user import User
from app.schemas.chat import ChatQueryRequest
from app.schemas.chat import ToolCallTrace
from app.services.agent_tool_service import AgentAnswer
from app.services.rag_service import RAGService
from app.services.vector_store_service import RetrievedChunk


class FakeRetriever:
    def __init__(self, chunk: RetrievedChunk) -> None:
        self.chunk = chunk

    async def retrieve(self, **kwargs) -> list[RetrievedChunk]:
        return [self.chunk]


class FakeLLMService:
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str] | None = None,
    ) -> str:
        assert "record unique phrase" in user_prompt
        assert "Image context:" in user_prompt
        assert "Figure 2 Record workflow" in user_prompt
        assert image_paths == []
        return "The record unique phrase is available for owner@example.com."


class FakeAgentToolService:
    async def answer_with_tools(self, **kwargs) -> AgentAnswer:
        return AgentAnswer(
            answer="The streamed response uses the retention policy.",
            latency_ms=12,
            tool_calls=[
                ToolCallTrace(
                    tool_name="search_documents",
                    arguments={"query": "retention policy"},
                    result={"results": [{"content": "retention policy"}]},
                )
            ],
        )


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "record-test"}


def _create_record_test_chunk() -> tuple[UUID, UUID, RetrievedChunk]:
    with SessionLocal() as db:
        kb = db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).first()
        assert kb is not None
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-record-test.pdf",
            original_filename="record-test.pdf",
            title="record-test",
            file_type="pdf",
            file_path="data/uploads/record-test.pdf",
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            chunk_count=1,
        )
        db.add(document)
        db.flush()
        chunk = DocumentChunk(
            document_id=document.id,
            knowledge_base_id=kb.id,
            chunk_index=0,
            content="record unique phrase with traceable context",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            chunk_metadata={"title": "record-test", "page_start": 1, "page_end": 1},
            embedding=[0.0] * 1024,
        )
        db.add(chunk)
        db.add(
            DocumentImage(
                document_id=document.id,
                knowledge_base_id=kb.id,
                page_number=1,
                image_index=1,
                caption="Figure 2 Record workflow",
                image_path="data/extracted_images/record-test/page-0001-image-001.png",
                mime_type="image/png",
                width=320,
                height=200,
                ocr_text="record workflow diagram",
                extraction_method="embedded_image",
                image_metadata={"source_name": "image.png"},
            )
        )
        db.commit()
        return (
            document.id,
            kb.id,
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                content=chunk.content,
                vector_score=0.8,
                keyword_score=0.4,
                final_score=0.66,
                metadata=chunk.chunk_metadata,
            ),
        )


def _cleanup_record_test_data(
    *,
    document_id: UUID,
    session_id: UUID | None,
    external_user_id: str,
    extra_external_user_ids: list[str] | None = None,
) -> None:
    with SessionLocal() as db:
        message_ids = []
        if session_id is not None:
            message_ids = [
                row[0]
                for row in db.query(ChatMessage.id)
                .filter(ChatMessage.session_id == session_id)
                .all()
            ]
        external_user_ids = [external_user_id, *(extra_external_user_ids or [])]
        users = db.query(User).filter(User.external_user_id.in_(external_user_ids)).all()
        if message_ids:
            db.execute(delete(RetrievalLog).where(RetrievalLog.message_id.in_(message_ids)))
            db.execute(delete(LLMCallLog).where(LLMCallLog.message_id.in_(message_ids)))
            db.execute(delete(MaskingEvent).where(MaskingEvent.message_id.in_(message_ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.target_id.in_(message_ids)))
            db.execute(delete(ChatMessage).where(ChatMessage.id.in_(message_ids)))
        for user in users:
            db.execute(delete(AuditEvent).where(AuditEvent.user_id == user.id))
        if session_id is not None:
            db.execute(delete(ChatSession).where(ChatSession.id == session_id))
        for user in users:
            db.execute(delete(User).where(User.id == user.id))
        db.execute(delete(DocumentImage).where(DocumentImage.document_id == document_id))
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.commit()


@pytest.mark.asyncio
async def test_rag_query_persists_traceable_records_and_audit_api() -> None:
    document_id, kb_id, chunk = _create_record_test_chunk()
    external_user_id = f"record-user-{uuid4()}"
    denied_external_user_id = f"record-denied-{uuid4()}"
    session_id = None
    try:
        with SessionLocal() as db:
            service = RAGService(db)
            service.retriever = FakeRetriever(chunk)
            service.llm_service = FakeLLMService()
            response = await service.answer(
                ChatQueryRequest(
                    knowledge_base_ids=[kb_id],
                    query="Summarize the figure for record unique phrase for person@example.com",
                    top_k=3,
                    use_rerank=False,
                    use_masking=True,
                ),
                request_id="record-test",
                principal=Principal(
                    external_user_id=external_user_id,
                    username=external_user_id,
                    department="finance",
                    roles={"employee"},
                    clearance_level=ConfidentialLevel.INTERNAL,
                ),
            )
            session_id = response.session_id
            user_message_id = response.message_id

            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == response.session_id)
                .order_by(ChatMessage.created_at.asc())
                .all()
            )
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[0].id == user_message_id
            assert messages[0].masked_content is not None
            assert "[EMAIL]" in messages[0].masked_content
            assert "[EMAIL]" in response.answer
            assert len(response.images) == 1
            assert response.images[0].caption == "Figure 2 Record workflow"

            retrieval_log = (
                db.query(RetrievalLog)
                .filter(RetrievalLog.message_id == user_message_id)
                .one()
            )
            assert retrieval_log.chunk_id == chunk.chunk_id
            assert retrieval_log.used_in_context is True

            llm_log = db.query(LLMCallLog).filter(LLMCallLog.message_id == user_message_id).one()
            assert llm_log.status == "success"
            assert llm_log.total_tokens is not None
            assert llm_log.total_tokens > 0

            assert (
                db.query(MaskingEvent)
                .filter(MaskingEvent.message_id == user_message_id)
                .count()
                >= 2
            )
            assert (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.event_type == "query_executed",
                    AuditEvent.target_id == user_message_id,
                )
                .count()
                == 1
            )

        client = TestClient(create_app())
        token = f"{external_user_id}|finance|internal|employee"
        session_response = client.get(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=_auth_headers(token),
        )
        assert session_response.status_code == 200
        assert [item["role"] for item in session_response.json()["messages"]] == ["user", "assistant"]

        audit_response = client.get(
            f"/api/v1/audit/chat-logs?user_id={external_user_id}",
            headers=_auth_headers("admin"),
        )
        assert audit_response.status_code == 200
        assert any(
            item["message_id"] == str(user_message_id)
            for item in audit_response.json()["items"]
        )

        masking_response = client.get("/api/v1/audit/masking-events", headers=_auth_headers("admin"))
        assert masking_response.status_code == 200
        assert any(
            item["message_id"] == str(user_message_id)
            for item in masking_response.json()["items"]
        )

        retrieval_response = client.get(
            f"/api/v1/audit/retrieval-logs?message_id={user_message_id}",
            headers=_auth_headers("admin"),
        )
        assert retrieval_response.status_code == 200
        retrieval_items = retrieval_response.json()["items"]
        assert any(
            item["message_id"] == str(user_message_id)
            and item["chunk_id"] == str(chunk.chunk_id)
            and item["used_in_context"] is True
            for item in retrieval_items
        )

        llm_response = client.get(
            f"/api/v1/audit/llm-logs?message_id={user_message_id}&status=success",
            headers=_auth_headers("admin"),
        )
        assert llm_response.status_code == 200
        assert any(
            item["message_id"] == str(user_message_id)
            and item["status"] == "success"
            and item["total_tokens"] > 0
            for item in llm_response.json()["items"]
        )

        denied_response = client.get(
            f"/api/v1/documents/{document_id}",
            headers=_auth_headers(f"{denied_external_user_id}|finance|public|employee"),
        )
        assert denied_response.status_code == 403

        permission_denied_response = client.get(
            f"/api/v1/audit/permission-denied?user_id={denied_external_user_id}",
            headers=_auth_headers("admin"),
        )
        assert permission_denied_response.status_code == 200
        assert any(
            item["event_type"] == "permission_denied"
            and item["target_type"] == "document"
            and item["target_id"] == str(document_id)
            for item in permission_denied_response.json()["items"]
        )
    finally:
        _cleanup_record_test_data(
            document_id=document_id,
            session_id=session_id,
            external_user_id=external_user_id,
            extra_external_user_ids=[denied_external_user_id],
        )


@pytest.mark.asyncio
async def test_rag_stream_uses_tool_loop_and_returns_tool_traces() -> None:
    document_id, kb_id, chunk = _create_record_test_chunk()
    external_user_id = f"stream-tool-user-{uuid4()}"
    session_id = None
    try:
        with SessionLocal() as db:
            service = RAGService(db)
            service.retriever = FakeRetriever(chunk)
            service.agent_tool_service = FakeAgentToolService()

            events = [
                event
                async for event in service.stream_answer(
                    ChatQueryRequest(
                        knowledge_base_ids=[kb_id],
                        query="Use tools to find retention policy",
                        top_k=3,
                        use_rerank=False,
                        use_masking=True,
                        use_tools=True,
                    ),
                    request_id="stream-tool-test",
                    principal=Principal(
                        external_user_id=external_user_id,
                        username=external_user_id,
                        department="finance",
                        roles={"employee"},
                        clearance_level=ConfidentialLevel.INTERNAL,
                    ),
                )
            ]

            done_event = events[-1]
            response = done_event["response"]
            session_id = response.session_id
            assert [event["event"] for event in events] == ["start", "delta", "done"]
            assert events[1]["text"] == "The streamed response uses the retention policy."
            assert response.answer == "The streamed response uses the retention policy."
            assert response.tool_calls[0].tool_name == "search_documents"
            assert response.tool_calls[0].result["results"][0]["content"] == "retention policy"

            audit_event = (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.event_type == "query_executed",
                    AuditEvent.target_id == response.message_id,
                )
                .one()
            )
            assert audit_event.event_metadata["tool_call_count"] == 1
            assert audit_event.event_metadata["tool_calls"][0]["tool_name"] == "search_documents"
    finally:
        _cleanup_record_test_data(
            document_id=document_id,
            session_id=session_id,
            external_user_id=external_user_id,
        )
