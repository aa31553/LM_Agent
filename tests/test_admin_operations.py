from datetime import datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.constants import ConfidentialLevel, DocumentStatus
from app.db.session import SessionLocal
from app.main import create_app
from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.masking import MaskingEvent
from app.models.masking import SensitiveDictionary
from app.models.user import User


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin", "X-Request-ID": "admin-ops-test"}


def _create_retention_test_data() -> tuple[UUID, UUID, UUID, UUID, UUID]:
    old = datetime(2000, 1, 1)
    with SessionLocal() as db:
        kb = KnowledgeBase(
            name=f"retention-test-{uuid4()}",
            description="retention test",
            owner_department="qa",
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add(kb)
        db.flush()
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-retention-test.pdf",
            original_filename="retention-test.pdf",
            title="retention-test",
            file_type="pdf",
            file_path="data/uploads/retention-test.pdf",
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            chunk_count=0,
            created_at=old,
            updated_at=old,
        )
        user = User(
            external_user_id=f"retention-user-{uuid4()}",
            username="retention-user",
            department="qa",
            clearance_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add_all([document, user])
        db.flush()
        session = ChatSession(user_id=user.id, title="retention-test", created_at=old, updated_at=old)
        db.add(session)
        db.flush()
        message = ChatMessage(
            session_id=session.id,
            user_id=user.id,
            role="user",
            original_content="retention test",
            risk_level="low",
            created_at=old,
        )
        db.add(message)
        db.flush()
        db.add_all(
            [
                RetrievalLog(message_id=message.id, query="retention", created_at=old),
                LLMCallLog(message_id=message.id, status="success", created_at=old),
                MaskingEvent(entity_type="email", masked_value="[EMAIL]", created_at=old),
                AuditEvent(event_type="permission_denied", message="old event", created_at=old),
            ]
        )
        db.commit()
        return document.id, kb.id, user.id, session.id, message.id


def _cleanup_retention_test_data(
    document_id: UUID,
    kb_id: UUID,
    user_id: UUID,
    session_id: UUID,
    message_id: UUID,
) -> None:
    with SessionLocal() as db:
        db.execute(delete(RetrievalLog).where(RetrievalLog.message_id == message_id))
        db.execute(delete(LLMCallLog).where(LLMCallLog.message_id == message_id))
        db.execute(delete(MaskingEvent).where(MaskingEvent.masked_value == "[EMAIL]"))
        db.execute(delete(AuditEvent).where(AuditEvent.message == "old event"))
        db.execute(delete(ChatMessage).where(ChatMessage.id == message_id))
        db.execute(delete(ChatSession).where(ChatSession.id == session_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.execute(delete(User).where(User.id == user_id))
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        db.commit()


def test_admin_retention_and_metrics_api() -> None:
    document_id, kb_id, user_id, session_id, message_id = _create_retention_test_data()
    client = TestClient(create_app())
    try:
        preview = client.post(
            "/api/v1/admin/retention",
            headers=_admin_headers(),
            json={"apply": False, "document_archive_after_days": 5000},
        )
        assert preview.status_code == 200
        assert preview.json()["applied"] is False
        assert preview.json()["documents_archived"] >= 1
        assert preview.json()["retrieval_logs_deleted"] >= 1

        applied = client.post(
            "/api/v1/admin/retention",
            headers=_admin_headers(),
            json={"apply": True, "document_archive_after_days": 5000},
        )
        assert applied.status_code == 200
        assert applied.json()["applied"] is True
        assert applied.json()["documents_archived"] >= 1
        assert applied.json()["retrieval_logs_deleted"] >= 1
        assert applied.json()["llm_logs_deleted"] >= 1
        assert applied.json()["masking_events_deleted"] >= 1
        assert applied.json()["audit_events_deleted"] >= 1

        with SessionLocal() as db:
            document = db.get(Document, document_id)
            assert document is not None
            assert document.status == DocumentStatus.ARCHIVED.value
            assert db.query(RetrievalLog).filter(RetrievalLog.message_id == message_id).count() == 0

        metrics = client.get("/api/v1/admin/operations/metrics", headers=_admin_headers())
        assert metrics.status_code == 200
        assert metrics.json()["database"]["status"] == "ok"
        assert "documents" in metrics.json()
    finally:
        _cleanup_retention_test_data(document_id, kb_id, user_id, session_id, message_id)


def test_admin_sensitive_rule_management_api() -> None:
    client = TestClient(create_app())
    value = f"Customer-{uuid4()}"
    try:
        created = client.post(
            "/api/v1/admin/sensitive-rules",
            headers=_admin_headers(),
            json={
                "entity_type": "customer_name",
                "value": value,
                "replacement": "[CUSTOMER_TEST]",
                "risk_level": "medium",
                "is_active": True,
            },
        )
        assert created.status_code == 200
        rule_id = created.json()["rule_id"]

        listed = client.get("/api/v1/admin/sensitive-rules", headers=_admin_headers())
        assert listed.status_code == 200
        assert any(item["value"] == value for item in listed.json()["items"])

        disabled = client.patch(
            f"/api/v1/admin/sensitive-rules/{rule_id}",
            headers=_admin_headers(),
            json={"is_active": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["is_active"] is False

        templates = client.post("/api/v1/admin/sensitive-rules/templates", headers=_admin_headers())
        assert templates.status_code == 200
        assert len(templates.json()["items"]) >= 3
    finally:
        with SessionLocal() as db:
            db.execute(delete(SensitiveDictionary).where(SensitiveDictionary.value == value))
            db.commit()
