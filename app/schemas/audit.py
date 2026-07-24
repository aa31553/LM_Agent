from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ChatLogItem(BaseModel):
    message_id: UUID
    user_id: UUID | str
    query: str
    risk_level: str
    created_at: datetime


class MaskingEventItem(BaseModel):
    event_id: UUID
    message_id: UUID | None = None
    entity_type: str
    masked_value: str | None = None
    masking_method: str | None = None
    risk_level: str | None = None
    created_at: datetime


class RetrievalLogItem(BaseModel):
    log_id: UUID
    message_id: UUID
    query: str
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    rerank_score: float | None = None
    final_score: float | None = None
    rank: int | None = None
    used_in_context: bool
    created_at: datetime


class LLMLogItem(BaseModel):
    log_id: UUID
    message_id: UUID
    model_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    status: str | None = None
    error_message: str | None = None
    created_at: datetime


class AuditEventItem(BaseModel):
    event_id: UUID
    user_id: UUID | None = None
    event_type: str
    target_type: str | None = None
    target_id: UUID | None = None
    risk_level: str | None = None
    message: str | None = None
    metadata: dict | None = None
    created_at: datetime
