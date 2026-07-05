from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseResponse
from app.services.permission_service import PermissionService

router = APIRouter()


@router.post("", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> KnowledgeBaseResponse:
    knowledge_base = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        owner_department=payload.owner_department,
        default_confidential_level=payload.default_confidential_level.value,
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


def _response(knowledge_base: KnowledgeBase, document_count: int) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        knowledge_base_id=knowledge_base.id,
        name=knowledge_base.name,
        owner_department=knowledge_base.owner_department,
        default_confidential_level=knowledge_base.default_confidential_level,
        document_count=document_count,
        is_active=knowledge_base.is_active,
    )
