from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.constants import ConfidentialLevel, DocumentStatus, PermissionLevel, PermissionSubjectType
from app.core.security import Principal
from app.db.session import SessionLocal
from app.main import create_app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.models.permission import DocumentPermission, KnowledgeBasePermission
from app.services.keyword_search_service import KeywordSearchService


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin", "X-Request-ID": "permission-test"}


def _principal_token(user: str, department: str) -> str:
    return f"{user}|{department}|internal|employee"


def _create_permission_test_document() -> tuple[UUID, UUID]:
    with SessionLocal() as db:
        kb = db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).first()
        assert kb is not None
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-permission-test.pdf",
            original_filename="permission-test.pdf",
            title="permission-test",
            file_type="pdf",
            file_path="data/uploads/permission-test.pdf",
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            chunk_count=1,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        chunk = DocumentChunk(
            document_id=document.id,
            knowledge_base_id=kb.id,
            chunk_index=0,
            content=f"permission unique phrase {document.id}",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            chunk_metadata={"title": "permission-test"},
            embedding=[0.0] * 1024,
        )
        db.add(chunk)
        db.commit()
        return document.id, kb.id


def _cleanup_permission_test_document(document_id: UUID) -> None:
    with SessionLocal() as db:
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.execute(delete(DocumentPermission).where(DocumentPermission.document_id == document_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.commit()


def _create_kb_permission_test_document() -> tuple[UUID, UUID]:
    with SessionLocal() as db:
        kb = KnowledgeBase(
            name=f"kb-permission-test-{uuid4()}",
            description="kb permission test",
            owner_department=None,
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add(kb)
        db.flush()
        document = Document(
            knowledge_base_id=kb.id,
            filename=f"{uuid4()}-kb-permission-test.pdf",
            original_filename="kb-permission-test.pdf",
            title="kb-permission-test",
            file_type="pdf",
            file_path="data/uploads/kb-permission-test.pdf",
            source_type="test",
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            chunk_count=0,
        )
        db.add(document)
        db.commit()
        return document.id, kb.id


def _cleanup_kb_permission_test_document(document_id: UUID, kb_id: UUID) -> None:
    with SessionLocal() as db:
        db.execute(delete(DocumentPermission).where(DocumentPermission.document_id == document_id))
        db.execute(
            delete(KnowledgeBasePermission).where(
                KnowledgeBasePermission.knowledge_base_id == kb_id
            )
        )
        db.execute(delete(Document).where(Document.id == document_id))
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        db.commit()


def test_document_permission_api_and_detail_access() -> None:
    document_id, _kb_id = _create_permission_test_document()
    client = TestClient(create_app())
    try:
        response = client.post(
            f"/api/v1/permissions/documents/{document_id}",
            headers=_admin_headers(),
            json={
                "subject_type": PermissionSubjectType.DEPARTMENT.value,
                "subject_value": "finance",
                "permission": PermissionLevel.READ.value,
            },
        )
        assert response.status_code == 200
        assert response.json()["subject_value"] == "finance"

        denied = client.get(
            f"/api/v1/documents/{document_id}",
            headers={"Authorization": f"Bearer {_principal_token('bob', 'engineering')}"},
        )
        assert denied.status_code == 403
        denied_list = client.get(
            "/api/v1/documents?page_size=100",
            headers={"Authorization": f"Bearer {_principal_token('bob', 'engineering')}"},
        )
        assert denied_list.status_code == 200
        assert all(item["document_id"] != str(document_id) for item in denied_list.json()["items"])

        allowed = client.get(
            f"/api/v1/documents/{document_id}",
            headers={"Authorization": f"Bearer {_principal_token('alice', 'finance')}"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["document_id"] == str(document_id)
        allowed_list = client.get(
            "/api/v1/documents?page_size=100",
            headers={"Authorization": f"Bearer {_principal_token('alice', 'finance')}"},
        )
        assert allowed_list.status_code == 200
        assert any(item["document_id"] == str(document_id) for item in allowed_list.json()["items"])

        permissions = client.get(
            f"/api/v1/permissions/documents/{document_id}",
            headers=_admin_headers(),
        )
        assert permissions.status_code == 200
        assert len(permissions.json()["permissions"]) == 1
    finally:
        _cleanup_permission_test_document(document_id)


def test_knowledge_base_permission_api_controls_document_access() -> None:
    document_id, kb_id = _create_kb_permission_test_document()
    client = TestClient(create_app())
    try:
        created = client.post(
            f"/api/v1/permissions/knowledge-bases/{kb_id}",
            headers=_admin_headers(),
            json={
                "subject_type": PermissionSubjectType.DEPARTMENT.value,
                "subject_value": "finance",
                "permission": PermissionLevel.READ.value,
            },
        )
        assert created.status_code == 200
        permission_id = created.json()["permission_id"]
        assert created.json()["subject_value"] == "finance"

        denied = client.get(
            f"/api/v1/documents/{document_id}",
            headers={"Authorization": f"Bearer {_principal_token('bob', 'engineering')}"},
        )
        assert denied.status_code == 403

        allowed = client.get(
            f"/api/v1/documents/{document_id}",
            headers={"Authorization": f"Bearer {_principal_token('alice', 'finance')}"},
        )
        assert allowed.status_code == 200

        listed = client.get(
            f"/api/v1/permissions/knowledge-bases/{kb_id}",
            headers=_admin_headers(),
        )
        assert listed.status_code == 200
        assert len(listed.json()["permissions"]) == 1

        deleted = client.delete(
            f"/api/v1/permissions/knowledge-bases/permissions/{permission_id}",
            headers=_admin_headers(),
        )
        assert deleted.status_code == 204
        listed_after_delete = client.get(
            f"/api/v1/permissions/knowledge-bases/{kb_id}",
            headers=_admin_headers(),
        )
        assert listed_after_delete.status_code == 200
        assert listed_after_delete.json()["permissions"] == []
    finally:
        _cleanup_kb_permission_test_document(document_id, kb_id)


@pytest.mark.asyncio
async def test_keyword_retrieval_applies_document_permissions() -> None:
    document_id, kb_id = _create_permission_test_document()
    with SessionLocal() as db:
        db.add(
            DocumentPermission(
                document_id=document_id,
                subject_type=PermissionSubjectType.DEPARTMENT.value,
                subject_value="finance",
                permission=PermissionLevel.READ.value,
            )
        )
        db.commit()

    try:
        with SessionLocal() as db:
            service = KeywordSearchService(db)
            denied = await service.search(
                query="permission unique phrase",
                knowledge_base_ids=[kb_id],
                top_k=5,
                principal=Principal(
                    external_user_id="bob",
                    username="bob",
                    department="engineering",
                    clearance_level=ConfidentialLevel.INTERNAL,
                ),
            )
            allowed = await service.search(
                query="permission unique phrase",
                knowledge_base_ids=[kb_id],
                top_k=5,
                principal=Principal(
                    external_user_id="alice",
                    username="alice",
                    department="finance",
                    clearance_level=ConfidentialLevel.INTERNAL,
                ),
            )
            assert all(chunk.document_id != document_id for chunk in denied)
            assert any(chunk.document_id == document_id for chunk in allowed)
    finally:
        _cleanup_permission_test_document(document_id)
