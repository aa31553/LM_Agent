from uuid import UUID, uuid4

import json
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.core.constants import ConfidentialLevel, DocumentStatus, PermissionLevel, PermissionSubjectType
from app.db.session import SessionLocal
from app.main import create_app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.models.permission import DocumentPermission, KnowledgeBasePermission
from app.services.llm_service import LLMService


async def _approved_topic_review(self, system_prompt: str, user_prompt: str, image_paths=None) -> str:
    payload = json.loads(user_prompt)
    candidate = payload["candidate"]
    return json.dumps(
        {
            "decision": "approved",
            "canonical_topic": candidate,
            "page_type": "concept",
            "aliases": [],
            "quality_score": 0.82,
            "confidence": 0.9,
            "rejection_reason": "",
            "related_topic_hints": [],
        }
    )


def _token(user: str, department: str) -> str:
    return f"{user}|{department}|internal|employee"


def _create_llmwiki_fixture() -> tuple[UUID, UUID]:
    with SessionLocal() as db:
        kb = KnowledgeBase(
            name=f"llmwiki-kb-{uuid4()}",
            description="LLMWiki API test",
            owner_department=None,
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add(kb)
        db.flush()
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-llmwiki.md",
            original_filename="llmwiki.md",
            title="LLMWiki Retrieval Design",
            file_type="markdown",
            file_path="data/uploads/llmwiki.md",
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
                        "LLMWiki AlphaTopic compiles enterprise documents into wiki pages. "
                        "AlphaTopic uses bidirectional links, evidence snippets, and graph nodes "
                        "so agents can traverse related knowledge quickly."
                    ),
                    section_title="AlphaTopic compilation",
                    confidential_level=ConfidentialLevel.INTERNAL.value,
                    chunk_metadata={"title": "AlphaTopic"},
                ),
                DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=kb.id,
                    chunk_index=1,
                    content=(
                        "AlphaTopic graph rendering connects source documents, evidence chunks, "
                        "and related topics such as PermissionFilter and KnowledgeGraph."
                    ),
                    section_title="AlphaTopic knowledge graph",
                    confidential_level=ConfidentialLevel.INTERNAL.value,
                    chunk_metadata={"title": "AlphaTopic graph"},
                ),
            ]
        )
        db.add(
            KnowledgeBasePermission(
                knowledge_base_id=kb.id,
                subject_type=PermissionSubjectType.DEPARTMENT.value,
                subject_value="finance",
                permission=PermissionLevel.READ.value,
            )
        )
        db.commit()
        return document.id, kb.id


def _cleanup_llmwiki_fixture(document_id: UUID, kb_id: UUID) -> None:
    with SessionLocal() as db:
        db.execute(
            text("DELETE FROM llmwiki_operation_logs WHERE metadata->>'knowledge_base_id' = :kb_id"),
            {"kb_id": str(kb_id)},
        )
        db.execute(
            text("DELETE FROM llmwiki_pages WHERE knowledge_base_id = :kb_id"),
            {"kb_id": str(kb_id)},
        )
        db.execute(
            text("DELETE FROM llmwiki_topics WHERE knowledge_base_id = :kb_id"),
            {"kb_id": str(kb_id)},
        )
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.execute(delete(DocumentPermission).where(DocumentPermission.document_id == document_id))
        db.execute(
            delete(KnowledgeBasePermission).where(KnowledgeBasePermission.knowledge_base_id == kb_id)
        )
        db.execute(delete(Document).where(Document.id == document_id))
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        db.commit()


def test_llmwiki_topic_page_and_graph_respect_knowledge_base_permissions(monkeypatch) -> None:
    monkeypatch.setattr(LLMService, "complete", _approved_topic_review)
    document_id, kb_id = _create_llmwiki_fixture()
    client = TestClient(create_app())
    params = [("knowledge_base_ids", str(kb_id)), ("top_k", "5")]
    try:
        denied = client.get(
            "/api/v1/llmwiki/topics/AlphaTopic",
            params=params,
            headers={"Authorization": f"Bearer {_token('bob', 'engineering')}"},
        )
        assert denied.status_code == 200
        assert denied.json()["evidence"] == []

        allowed = client.get(
            "/api/v1/llmwiki/topics/AlphaTopic",
            params=params,
            headers={"Authorization": f"Bearer {_token('alice', 'finance')}"},
        )
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["topic"] == "AlphaTopic"
        assert len(body["evidence"]) == 2
        assert body["graph"]["nodes"]
        assert any(edge["relation"] == "supported_by" for edge in body["graph"]["edges"])

        search = client.get(
            "/api/v1/llmwiki/search",
            params=[("knowledge_base_ids", str(kb_id)), ("q", "AlphaTopic"), ("limit", "5")],
            headers={"Authorization": f"Bearer {_token('alice', 'finance')}"},
        )
        assert search.status_code == 200
        assert any(item["topic"] == "AlphaTopic" for item in search.json()["items"])
    finally:
        _cleanup_llmwiki_fixture(document_id, kb_id)


def test_llmwiki_compile_index_lint_and_demo_api(monkeypatch) -> None:
    monkeypatch.setattr(LLMService, "complete", _approved_topic_review)
    document_id, kb_id = _create_llmwiki_fixture()
    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {_token('alice', 'finance')}"}
    params = [("knowledge_base_ids", str(kb_id)), ("top_k", "8")]
    try:
        compiled = client.post(
            "/api/v1/llmwiki/topics/AlphaTopic/compile",
            params=params,
            headers=headers,
        )
        assert compiled.status_code == 200
        compiled_body = compiled.json()
        assert compiled_body["operation"] in {"created", "updated"}
        page = compiled_body["page"]
        assert page["compiled_page_id"]
        assert page["compiled_slug"] == "alphatopic"
        assert page["source_chunk_count"] == 2
        assert "## Evidence" in page["content_markdown"]
        assert page["key_points"]

        index = client.get(
            "/api/v1/llmwiki/index",
            params=[("knowledge_base_ids", str(kb_id))],
            headers=headers,
        )
        assert index.status_code == 200
        assert any(item["topic"] == "AlphaTopic" for item in index.json()["items"])

        loaded = client.get(
            "/api/v1/llmwiki/topics/AlphaTopic",
            params=[("knowledge_base_ids", str(kb_id)), ("prefer_compiled", "true")],
            headers=headers,
        )
        assert loaded.status_code == 200
        assert loaded.json()["compiled_page_id"] == page["compiled_page_id"]

        lint = client.get(
            "/api/v1/llmwiki/lint",
            params=[("knowledge_base_ids", str(kb_id))],
            headers=headers,
        )
        assert lint.status_code == 200
        assert lint.json()["checked_pages"] >= 1

        demo = client.get("/api/v1/llmwiki/demo", headers=headers)
        assert demo.status_code == 200
        assert demo.json()["topic"] == "LLMWiki Compounding Knowledge"
    finally:
        _cleanup_llmwiki_fixture(document_id, kb_id)
