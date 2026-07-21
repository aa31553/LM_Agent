from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.constants import RiskLevel


class ChatQueryRequest(BaseModel):
    session_id: UUID | None = None
    knowledge_base_ids: list[UUID] = Field(default_factory=list)
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    use_rerank: bool = True
    use_masking: bool = True
    use_tools: bool = False


class ToolCallTrace(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)


class Citation(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    section_title: str | None = None
    score: float


class ImageReference(BaseModel):
    image_id: UUID
    document_id: UUID
    page_number: int
    caption: str | None = None
    image_path: str
    ocr_text: str | None = None


class MaskedEntity(BaseModel):
    entity_type: str
    masked_value: str


class ChatQueryResponse(BaseModel):
    request_id: str
    session_id: UUID
    message_id: UUID
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    images: list[ImageReference] = Field(default_factory=list)
    confidence: RiskLevel | str = RiskLevel.INSUFFICIENT
    risk_level: RiskLevel = RiskLevel.LOW
    masked_entities: list[MaskedEntity] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class ChatMessage(BaseModel):
    message_id: UUID
    role: str
    content: str
    created_at: datetime


class ChatSessionMessages(BaseModel):
    session_id: UUID
    messages: list[ChatMessage]


class ChatSessionDeleteResponse(BaseModel):
    session_id: UUID
    deleted_documents: int
    deleted_messages: int
    deleted_files: int
    status: str = "deleted"
