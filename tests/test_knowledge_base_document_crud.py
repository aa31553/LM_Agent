from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.v1.documents import delete_document, list_documents, update_document
from app.api.v1.knowledge_bases import (
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base,
    update_knowledge_base,
)
from app.core.config import settings
from app.core.constants import ConfidentialLevel, DocumentStatus
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.base import Base
from app.db.session import create_database_engine
from app.models.document import Document, DocumentProcessingJob
from app.models.knowledge_base import KnowledgeBase
from app.schemas.document import DocumentUpdate
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.audit_service import AuditService


def _principal(user: str, department: str) -> Principal:
    return Principal(
        external_user_id=user,
        username=user,
        department=department,
        roles={"employee"},
        clearance_level=ConfidentialLevel.INTERNAL,
    )


@pytest.mark.asyncio
async def test_knowledge_base_and_document_crud_enforces_write_and_admin_permissions(
    tmp_path: Path, monkeypatch
) -> None:
    engine = create_database_engine("sqlite:///:memory:")
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    monkeypatch.setattr(settings, "local_storage_root", str(tmp_path / "uploads"))
    owner = _principal("alice", "quality")
    writer = _principal("bob", "engineering")
    uploader = _principal("carol", "production")
    try:
        created = await create_knowledge_base(
            KnowledgeBaseCreate(name="Quality Manual", owner_department="quality"), owner, db
        )
        knowledge_base = db.get(KnowledgeBase, created.knowledge_base_id)
        assert knowledge_base is not None
        assert (await get_knowledge_base(knowledge_base.id, owner, db)).name == "Quality Manual"

        with pytest.raises(APIError) as denied_update:
            await update_knowledge_base(
                knowledge_base.id, KnowledgeBaseUpdate(name="No access"), writer, db
            )
        assert denied_update.value.status_code == 403

        owner_user = AuditService(db).ensure_user(owner)
        uploader_user = AuditService(db).ensure_user(uploader)
        document_path = tmp_path / "uploads" / "originals" / "manual.txt"
        document_path.parent.mkdir(parents=True)
        document_path.write_text("quality manual", encoding="utf-8")
        document = Document(
            knowledge_base_id=knowledge_base.id,
            filename="manual.txt",
            original_filename="manual.txt",
            title="Manual",
            file_type="txt",
            file_path=str(document_path),
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            created_by=uploader_user.id,
        )
        db.add(document)
        db.flush()
        db.add(DocumentProcessingJob(document_id=document.id, job_type="document", status="queued"))
        db.commit()

        with pytest.raises(APIError) as denied_document_update:
            await update_document(document.id, DocumentUpdate(title="No access"), writer, db)
        assert denied_document_update.value.status_code == 403

        # KB owners can manage their KB's documents; ordinary users cannot until granted WRITE.
        from app.models.permission import KnowledgeBasePermission

        db.add(
            KnowledgeBasePermission(
                knowledge_base_id=knowledge_base.id,
                subject_type="user",
                subject_value=writer.external_user_id,
                permission="write",
            )
        )
        db.commit()
        updated = await update_document(document.id, DocumentUpdate(title="Writer update"), writer, db)
        assert updated.title == "Writer update"

        page = await list_documents(knowledge_base.id, None, None, 1, 20, writer, db)
        assert page.total == 1
        assert page.items[0].document_id == document.id

        # WRITE does not permit destructive deletion; KB owner can delete and removes the file and job.
        with pytest.raises(APIError) as denied_delete:
            await delete_document(document.id, writer, db)
        assert denied_delete.value.status_code == 403
        response = await delete_document(document.id, owner, db)
        assert response.status_code == 204
        assert db.get(Document, document.id) is None
        assert db.scalar(select(DocumentProcessingJob).where(DocumentProcessingJob.document_id == document.id)) is None
        assert not document_path.exists()

        # Knowledge-base deletion also removes its documents and associated storage.
        second_path = tmp_path / "uploads" / "originals" / "to-delete.txt"
        second_path.parent.mkdir(parents=True, exist_ok=True)
        second_path.write_text("delete", encoding="utf-8")
        second = Document(
            knowledge_base_id=knowledge_base.id,
            filename=f"{uuid4()}.txt",
            file_type="txt",
            file_path=str(second_path),
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=DocumentStatus.READY.value,
            created_by=owner_user.id,
        )
        db.add(second)
        db.commit()
        response = await delete_knowledge_base(knowledge_base.id, owner, db)
        assert response.status_code == 204
        assert db.get(KnowledgeBase, knowledge_base.id) is None
        assert db.get(Document, second.id) is None
        assert not second_path.exists()
    finally:
        db.close()
        engine.dispose()
