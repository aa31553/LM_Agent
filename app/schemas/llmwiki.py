from uuid import UUID

from pydantic import BaseModel, Field


class LLMWikiSearchResponse(BaseModel):
    items: list["LLMWikiTopicCandidate"]


class LLMWikiTopicCandidate(BaseModel):
    topic: str
    score: float = 0.0
    evidence_count: int = 0
    document_count: int = 0
    related_topics: list[str] = Field(default_factory=list)
    page_type: str = "unknown"
    status: str = "candidate"
    quality_score: float = 0.0


class LLMWikiEvidence(BaseModel):
    chunk_id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    document_title: str | None = None
    section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str
    confidential_level: str
    score: float = 0.0
    source_type: str | None = None


class LLMWikiLink(BaseModel):
    topic: str
    direction: str
    strength: float = 0.0
    evidence_count: int = 0
    page_type: str = "unknown"
    quality_score: float = 0.0


class LLMWikiGraphNode(BaseModel):
    id: str
    label: str
    type: str
    weight: float = 1.0


class LLMWikiGraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    weight: float = 1.0
    evidence_chunk_ids: list[UUID] = Field(default_factory=list)


class LLMWikiGraph(BaseModel):
    nodes: list[LLMWikiGraphNode]
    edges: list[LLMWikiGraphEdge]


class LLMWikiTopicPage(BaseModel):
    topic: str
    summary: str
    page_type: str = "unknown"
    key_points: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    linked_topics: list[LLMWikiLink] = Field(default_factory=list)
    evidence: list[LLMWikiEvidence] = Field(default_factory=list)
    graph: LLMWikiGraph | None = None
    content_markdown: str | None = None
    compiled_page_id: UUID | None = None
    compiled_slug: str | None = None
    last_compiled_at: str | None = None
    source_document_count: int = 0
    source_chunk_count: int = 0
    stale: bool = False
    topic_quality_score: float = 0.0
    contradictions: list[str] = Field(default_factory=list)
    maintenance_notes: list[str] = Field(default_factory=list)


class LLMWikiCompileResponse(BaseModel):
    page: LLMWikiTopicPage
    operation: str


class LLMWikiIndexItem(BaseModel):
    page_id: UUID
    knowledge_base_id: UUID
    topic: str
    slug: str
    summary: str
    linked_topics: list[str] = Field(default_factory=list)
    source_document_count: int = 0
    source_chunk_count: int = 0
    stale: bool = False
    updated_at: str


class LLMWikiIndexResponse(BaseModel):
    items: list[LLMWikiIndexItem]


class LLMWikiLintIssue(BaseModel):
    code: str
    severity: str
    message: str
    topic: str | None = None
    page_id: UUID | None = None


class LLMWikiLintResponse(BaseModel):
    checked_pages: int
    issues: list[LLMWikiLintIssue] = Field(default_factory=list)
    suggested_topics: list[str] = Field(default_factory=list)


class LLMWikiOperationLogItem(BaseModel):
    operation_id: UUID
    operation: str
    topic: str | None = None
    message: str
    metadata: dict = Field(default_factory=dict)
    created_at: str


class LLMWikiOperationLogResponse(BaseModel):
    items: list[LLMWikiOperationLogItem]
