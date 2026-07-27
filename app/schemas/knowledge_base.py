from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.constants import ConfidentialLevel


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    owner_department: str | None = None
    default_confidential_level: ConfidentialLevel = ConfidentialLevel.INTERNAL


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    owner_department: str | None = None
    default_confidential_level: ConfidentialLevel | None = None
    is_active: bool | None = None


class KnowledgeBaseResponse(BaseModel):
    knowledge_base_id: UUID
    name: str
    description: str | None = None
    owner_department: str | None = None
    default_confidential_level: ConfidentialLevel = ConfidentialLevel.INTERNAL
    document_count: int = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
