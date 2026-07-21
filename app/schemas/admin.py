from pydantic import BaseModel, Field

from app.core.constants import RiskLevel


class RetentionPolicyRequest(BaseModel):
    apply: bool = False
    document_archive_after_days: int = Field(default=365, ge=1)
    chat_message_retention_days: int = Field(default=365, ge=1)
    retrieval_log_retention_days: int = Field(default=90, ge=1)
    masking_event_retention_days: int = Field(default=180, ge=1)
    llm_log_retention_days: int = Field(default=180, ge=1)
    audit_event_retention_days: int = Field(default=365, ge=1)


class RetentionPolicyResponse(BaseModel):
    applied: bool
    documents_archived: int
    chat_messages_deleted: int
    retrieval_logs_deleted: int
    masking_events_deleted: int
    llm_logs_deleted: int
    audit_events_deleted: int


class OperationMetricsResponse(BaseModel):
    database: dict[str, object]
    documents: dict[str, int]
    processing_jobs: dict[str, int]
    audit: dict[str, int]


class LLMTestRequest(BaseModel):
    system_prompt: str = Field(
        default="You are a helpful software engineering assistant.",
        min_length=1,
        max_length=4_000,
    )
    message: str = Field(min_length=1, max_length=20_000)


class LLMTestResponse(BaseModel):
    status: str
    model: str
    endpoint: str
    latency_ms: float
    answer: str


class EmbeddingTestRequest(BaseModel):
    text: str = Field(
        default="LM Agent embedding connectivity test.",
        min_length=1,
        max_length=20_000,
    )


class EmbeddingTestResponse(BaseModel):
    status: str
    model: str
    endpoint: str
    configured_dimension: int
    actual_dimension: int
    latency_ms: float
    vector_norm: float
    vector_preview: list[float]


class EmbeddingStatusResponse(BaseModel):
    status: str
    endpoint: str
    configured_model: str
    configured_dimension: int
    service: dict[str, object]


class SensitiveRuleRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1)
    replacement: str | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    is_active: bool = True


class SensitiveRuleStatusRequest(BaseModel):
    is_active: bool


class SensitiveRuleResponse(BaseModel):
    rule_id: str
    entity_type: str
    value: str
    replacement: str | None = None
    risk_level: str
    is_active: bool
