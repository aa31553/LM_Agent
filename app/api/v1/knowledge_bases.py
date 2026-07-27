from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.permission import KnowledgeBasePermission
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.services.audit_service import AuditService
from app.services.document_ingestion_service import DocumentIngestionService
from app.services.permission_service import PermissionService

router = APIRouter()


@router.post("", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> KnowledgeBaseResponse:
    if not principal.is_active:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Inactive users cannot create knowledge bases.", 403)
    user = AuditService(db).ensure_user(principal)
    knowledge_base = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        owner_department=payload.owner_department,
        default_confidential_level=payload.default_confidential_level.value,
        created_by=user.id,
    )
    db.add(knowledge_base)
    db.commit()
    db.refresh(knowledge_base)
    return _response(knowledge_base, document_count=0)


@router.get("", response_model=dict[str, list[KnowledgeBaseResponse]])
async def list_knowledge_bases(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[KnowledgeBaseResponse]]:
    rows = db.execute(
        select(KnowledgeBase, func.count(Document.id))
        .outerjoin(Document, Document.knowledge_base_id == KnowledgeBase.id)
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.created_at.desc())
    ).all()
    permission_service = PermissionService(db)
    return {
        "items": [
            _response(knowledge_base, int(document_count or 0))
            for knowledge_base, document_count in rows
            if permission_service.can_access_knowledge_base(principal, knowledge_base)
        ]
    }


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> KnowledgeBaseResponse:
    knowledge_base = _get_knowledge_base(db, knowledge_base_id)
    PermissionService(db).ensure_knowledge_base_read(principal, knowledge_base)
    return _response(knowledge_base, _document_count(db, knowledge_base.id))


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> KnowledgeBaseResponse:
    knowledge_base = _get_knowledge_base(db, knowledge_base_id)
    PermissionService(db).ensure_knowledge_base_manage(principal, knowledge_base)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "default_confidential_level" and value is not None:
            value = value.value
        setattr(knowledge_base, field, value)
    knowledge_base.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(knowledge_base)
    return _response(knowledge_base, _document_count(db, knowledge_base.id))


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    knowledge_base = _get_knowledge_base(db, knowledge_base_id)
    PermissionService(db).ensure_knowledge_base_manage(principal, knowledge_base)
    document_ids = list(
        db.scalars(select(Document.id).where(Document.knowledge_base_id == knowledge_base.id))
    )
    document_service = DocumentIngestionService(db=db)
    for document_id in document_ids:
        document_service.delete(document_id)
    db.execute(
        delete(KnowledgeBasePermission).where(
            KnowledgeBasePermission.knowledge_base_id == knowledge_base.id
        )
    )
    db.delete(knowledge_base)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _response(knowledge_base: KnowledgeBase, document_count: int) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        knowledge_base_id=knowledge_base.id,
        name=knowledge_base.name,
        description=knowledge_base.description,
        owner_department=knowledge_base.owner_department,
        default_confidential_level=knowledge_base.default_confidential_level,
        document_count=document_count,
        is_active=knowledge_base.is_active,
        created_at=knowledge_base.created_at,
        updated_at=knowledge_base.updated_at,
    )


def _get_knowledge_base(db: Session, knowledge_base_id: UUID) -> KnowledgeBase:
    knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise APIError(ErrorCode.INVALID_REQUEST, "Knowledge base not found.", 404)
    return knowledge_base


def _document_count(db: Session, knowledge_base_id: UUID) -> int:
    return int(
        db.scalar(select(func.count(Document.id)).where(Document.knowledge_base_id == knowledge_base_id))
        or 0
    )
