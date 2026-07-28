from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text

from app.core.constants import ConfidentialLevel, DocumentStatus
from app.core.security import Principal
from app.db.session import SessionLocal
from app.integrations.openai_compatible_client import ChatCompletionResult
from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.models.masking import MaskingEvent
from app.models.user import User
from app.schemas.chat import ChatQueryRequest
from app.services.answer_quality_service import AnswerQualityService
from app.services.llmwiki_service import LLMWikiService
from app.services.rag_service import RAGService


class EmptyRetriever:
    async def retrieve(self, **kwargs) -> list:
        return []


class LLMWikiReferenceLLM:
    async def complete_messages(self, messages, tools=None, tool_choice=None):
        user_prompt = messages[-1]["content"]
        assert "LLMWiki compiled knowledge context:" in user_prompt
        assert "[LLMWiki:alphatopic-quality]" in user_prompt
        assert "durable compiled knowledge" in user_prompt
        return ChatCompletionResult(
            content=(
                "1. Answer\n"
                "AlphaTopic Quality should use durable compiled knowledge pages that preserve "
                "evidence, cross-links, and maintenance notes across questions.\n"
                "2. Key points\n"
                "- Compile summaries once and keep evidence attached.\n"
                "- Use cross-links to connect related topics.\n"
                "- Review maintenance notes before operational use.\n"
                "3. Sources\n"
                "- LLMWiki: alphatopic-quality compiled page.\n"
                "4. Confidence\n"
                "High for the test fixture.\n"
                "5. Limitations\n"
                "This answer is limited to the compiled LLMWiki page."
            ),
            tool_calls=[],
        )


def _create_wiki_fixture() -> tuple[UUID, UUID]:
    with SessionLocal() as db:
        kb = KnowledgeBase(
            name=f"llmwiki-rag-kb-{uuid4()}",
            description="LLMWiki RAG integration test",
            owner_department=None,
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add(kb)
        db.flush()
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-llmwiki-rag.md",
            original_filename="llmwiki-rag.md",
            title="AlphaTopic Quality",
            file_type="markdown",
            file_path="data/uploads/llmwiki-rag.md",
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            chunk_count=2,
        )
        db.add(document)
        db.flush()
        db.add_all(
            [
                DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=kb.id,
                    chunk_index=0,
                    content=(
                        "AlphaTopic Quality uses durable compiled knowledge pages. "
                        "Each page preserves evidence, key points, cross-links, and maintenance notes."
                    ),
                    section_title="AlphaTopic Quality durable knowledge",
                    confidential_level=ConfidentialLevel.INTERNAL.value,
                    chunk_metadata={"title": "AlphaTopic Quality"},
                ),
                DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=kb.id,
                    chunk_index=1,
                    content=(
                        "The LLM answer should reference compiled wiki context when raw RAG chunks "
                        "are absent, and it should still explain sources and limitations."
                    ),
                    section_title="LLMWiki answer reference policy",
                    confidential_level=ConfidentialLevel.INTERNAL.value,
                    chunk_metadata={"title": "LLMWiki answer reference policy"},
                ),
            ]
        )
        db.commit()

    principal = Principal(
        external_user_id=f"wiki-compiler-{uuid4()}",
        username="wiki-compiler",
        department="qa",
        roles={"admin"},
        clearance_level=ConfidentialLevel.INTERNAL,
    )
    with SessionLocal() as db:
        LLMWikiService(db).compile_topic(
            topic="AlphaTopic Quality",
            knowledge_base_ids=[kb.id],
            top_k=8,
            include_graph=True,
            principal=principal,
        )
    return document.id, kb.id


def _cleanup_wiki_fixture(document_id: UUID, kb_id: UUID, session_id: UUID | None, user_id: str) -> None:
    with SessionLocal() as db:
        message_ids = []
        if session_id is not None:
            message_ids = [
                row[0]
                for row in db.query(ChatMessage.id).filter(ChatMessage.session_id == session_id).all()
            ]
        if message_ids:
            db.execute(delete(RetrievalLog).where(RetrievalLog.message_id.in_(message_ids)))
            db.execute(delete(LLMCallLog).where(LLMCallLog.message_id.in_(message_ids)))
            db.execute(delete(MaskingEvent).where(MaskingEvent.message_id.in_(message_ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.target_id.in_(message_ids)))
            db.execute(delete(ChatMessage).where(ChatMessage.id.in_(message_ids)))
        if session_id is not None:
            db.execute(delete(ChatSession).where(ChatSession.id == session_id))
        user = db.query(User).filter(User.external_user_id == user_id).first()
        if user is not None:
            db.execute(delete(AuditEvent).where(AuditEvent.user_id == user.id))
            db.execute(delete(User).where(User.id == user.id))
        db.execute(
            text("DELETE FROM llmwiki_operation_logs WHERE metadata->>'knowledge_base_id' = :kb_id"),
            {"kb_id": str(kb_id)},
        )
        db.execute(text("DELETE FROM llmwiki_pages WHERE knowledge_base_id = :kb_id"), {"kb_id": str(kb_id)})
        db.execute(text("DELETE FROM llmwiki_topics WHERE knowledge_base_id = :kb_id"), {"kb_id": str(kb_id)})
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        db.commit()


@pytest.mark.asyncio
async def test_rag_uses_llmwiki_compiled_context_and_scores_answer_quality() -> None:
    document_id, kb_id = _create_wiki_fixture()
    external_user_id = f"llmwiki-rag-user-{uuid4()}"
    session_id = None
    try:
        with SessionLocal() as db:
            service = RAGService(db)
            service.retriever = EmptyRetriever()
            service.llm_service = LLMWikiReferenceLLM()
            response = await service.answer(
                ChatQueryRequest(
                    knowledge_base_ids=[kb_id],
                    query="How should AlphaTopic Quality support durable compiled knowledge?",
                    top_k=3,
                    use_rerank=False,
                    use_masking=True,
                ),
                request_id="llmwiki-rag-quality-test",
                principal=Principal(
                    external_user_id=external_user_id,
                    username=external_user_id,
                    department="qa",
                    roles={"employee"},
                    clearance_level=ConfidentialLevel.INTERNAL,
                ),
            )
            session_id = response.session_id
            assert response.answer
            assert response.citations == []

            audit_event = (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.event_type == "query_executed",
                    AuditEvent.target_id == response.message_id,
                )
                .one()
            )
            assert audit_event.event_metadata["retrieved_chunks"] == 0
            assert audit_event.event_metadata["llmwiki_context_used"] is True

        quality = AnswerQualityService().score_grounded_answer(
            answer=response.answer,
            required_terms=["durable compiled knowledge", "evidence", "cross-links", "Sources"],
            forbidden_terms=["not enough information"],
        )
        assert quality.passed is True
        assert quality.score >= 95
    finally:
        _cleanup_wiki_fixture(document_id, kb_id, session_id, external_user_id)
