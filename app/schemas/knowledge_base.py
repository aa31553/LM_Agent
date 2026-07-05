from uuid import UUID

from pydantic import BaseModel

from app.core.constants import ConfidentialLevel


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str | None = None
    owner_department: str | None = None
    default_confidential_level: ConfidentialLevel = ConfidentialLevel.INTERNAL


class KnowledgeBaseResponse(BaseModel):
    knowledge_base_id: UUID
    name: str
    owner_department: str | None = None
    default_confidential_level: ConfidentialLevel = ConfidentialLevel.INTERNAL
    document_count: int = 0
    is_active: bool = True

