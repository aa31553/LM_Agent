from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.constants import ChatType, RetrievalScope, RiskLevel, ThinkingMode


class ChatQueryRequest(BaseModel):
    session_id: UUID | None = None
    workspace_id: UUID | None = None
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    retrieval_scope: RetrievalScope = RetrievalScope.AUTO
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    use_rerank: bool = True
    use_masking: bool = True
    use_tools: bool = False
    model: str | None = Field(default=None, min_length=1, max_length=128)
    thinking_mode: ThinkingMode = ThinkingMode.DEFAULT

    @model_validator(mode="after")
    def validate_retrieval_scope(self):
        session_scopes = {
            RetrievalScope.ATTACHMENTS_ONLY,
            RetrievalScope.SESSION_ATTACHMENTS,
            RetrievalScope.SESSION_AND_KNOWLEDGE_BASES,
        }
        if self.attachment_ids and self.session_id is None:
            raise ValueError("session_id is required when attachment_ids are supplied")
        if self.retrieval_scope in session_scopes and self.session_id is None:
            raise ValueError("session_id is required for the selected retrieval_scope")
        if (
            self.retrieval_scope == RetrievalScope.ATTACHMENTS_ONLY
            and not self.attachment_ids
        ):
            raise ValueError("attachment_ids is required for attachments_only")
        if (
            self.retrieval_scope == RetrievalScope.KNOWLEDGE_BASES_ONLY
            and not self.knowledge_base_ids
        ):
            raise ValueError("knowledge_base_ids is required for knowledge_bases_only")
        return self


class CodeChatRequest(ChatQueryRequest):
    code: str | None = Field(default=None, max_length=200_000)
    language: str | None = Field(default=None, max_length=32)
    file_name: str | None = Field(default=None, max_length=255)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    def model_post_init(self, __context) -> None:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be greater than or equal to line_start")


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


class LLMUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class AnswerSections(BaseModel):
    key_points: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ChatQueryResponse(BaseModel):
    request_id: str
    session_id: UUID
    message_id: UUID
    answer: str
    sections: AnswerSections = Field(default_factory=AnswerSections)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    citations: list[Citation] = Field(default_factory=list)
    images: list[ImageReference] = Field(default_factory=list)
    confidence: RiskLevel | str = RiskLevel.INSUFFICIENT
    risk_level: RiskLevel = RiskLevel.LOW
    masked_entities: list[MaskedEntity] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class CodeDiagnosis(BaseModel):
    summary: str | None = None
    confidence: str | None = None


class SuggestedCodeChange(BaseModel):
    title: str
    description: str | None = None
    code: str | None = None
    language: str | None = None


class CodeBlock(BaseModel):
    language: str | None = None
    code: str


class CodeChatResponse(ChatQueryResponse):
    chat_type: ChatType = ChatType.CODE
    diagnosis: CodeDiagnosis = Field(default_factory=CodeDiagnosis)
    suggested_changes: list[SuggestedCodeChange] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    code_blocks: list[CodeBlock] = Field(default_factory=list)


class ChatMessage(BaseModel):
    message_id: UUID
    role: str
    content: str
    created_at: datetime


class ChatSessionMessages(BaseModel):
    session_id: UUID
    messages: list[ChatMessage]


class SessionAttachmentItem(BaseModel):
    document_id: UUID
    filename: str
    file_type: str
    status: str
    source_type: str
    page_count: int | None = None
    chunk_count: int = 0
    created_at: datetime


class SessionAttachmentListResponse(BaseModel):
    session_id: UUID
    items: list[SessionAttachmentItem]


class SessionAttachmentDeleteResponse(BaseModel):
    session_id: UUID
    document_id: UUID
    deleted_files: int
    status: str = "deleted"


class ChatSessionDeleteResponse(BaseModel):
    session_id: UUID
    deleted_documents: int
    deleted_analysis_files: int = 0
    deleted_messages: int
    deleted_files: int
    status: str = "deleted"
