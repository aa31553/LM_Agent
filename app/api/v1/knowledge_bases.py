from uuid import uuid4

from fastapi import APIRouter, Depends

from app.core.security import Principal, get_current_principal
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseResponse

router = APIRouter()


@router.post("", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: Principal = Depends(get_current_principal),
) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        knowledge_base_id=uuid4(),
        name=payload.name,
        owner_department=payload.owner_department,
        default_confidential_level=payload.default_confidential_level,
        document_count=0,
        is_active=True,
    )


@router.get("", response_model=dict[str, list[KnowledgeBaseResponse]])
async def list_knowledge_bases(
    principal: Principal = Depends(get_current_principal),
) -> dict[str, list[KnowledgeBaseResponse]]:
    return {"items": []}

