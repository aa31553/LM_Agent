from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.constants import ConfidentialLevel, DocumentScope, DocumentStatus


class DocumentFormatItem(BaseModel):
    extension: str
    file_type: str
    category: str


class DocumentFormatsResponse(BaseModel):
    items: list[DocumentFormatItem]
    accept: str


class DocumentUploadResponse(BaseModel):
    request_id: str
    document_id: UUID
    status: DocumentStatus
    scope: DocumentScope
    knowledge_base_id: UUID | None = None
    session_id: UUID | None = None
    message: str


class DocumentListItem(BaseModel):
    document_id: UUID
    filename: str
    title: str | None = None
    status: DocumentStatus
    confidential_level: ConfidentialLevel
    scope: DocumentScope
    knowledge_base_id: UUID | None = None
    session_id: UUID | None = None
    department: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    ocr_required: bool = False
    ocr_confidence: float | None = None
    error_message: str | None = None
    created_at: datetime


class DocumentStatusResponse(BaseModel):
    document_id: UUID
    status: DocumentStatus
    progress: int = Field(ge=0, le=100)
    message: str


class DocumentDetail(BaseModel):
    document_id: UUID
    filename: str
    title: str | None = None
    scope: DocumentScope
    knowledge_base_id: UUID | None = None
    session_id: UUID | None = None
    language: str | None = None
    confidential_level: ConfidentialLevel
    department: str | None = None
    status: DocumentStatus
    page_count: int | None = None
    chunk_count: int = 0
    ocr_required: bool = False
    ocr_confidence: float | None = None
    error_message: str | None = None
    file_type: str
    source_type: str | None = None
    version: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentArchiveResponse(BaseModel):
    document_id: UUID
    status: DocumentStatus


class ReindexResponse(BaseModel):
    request_id: str
    document_id: UUID
    status: str
    message: str
