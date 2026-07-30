from enum import Enum


class TextEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ConfidentialLevel(TextEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


CONFIDENTIALITY_RANK: dict[ConfidentialLevel, int] = {
    ConfidentialLevel.PUBLIC: 1,
    ConfidentialLevel.INTERNAL: 2,
    ConfidentialLevel.CONFIDENTIAL: 3,
    ConfidentialLevel.RESTRICTED: 4,
}


class DocumentStatus(TextEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    OCR_PROCESSING = "ocr_processing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class DocumentScope(TextEnum):
    KNOWLEDGE_BASE = "knowledge_base"
    SESSION = "session"


class PermissionSubjectType(TextEnum):
    USER = "user"
    ROLE = "role"
    DEPARTMENT = "department"


class PermissionLevel(TextEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class MessageRole(TextEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatType(TextEnum):
    GENERAL = "general"
    CODE = "code"


class RetrievalScope(TextEnum):
    AUTO = "auto"
    ATTACHMENTS_ONLY = "attachments_only"
    SESSION_ATTACHMENTS = "session_attachments"
    KNOWLEDGE_BASES_ONLY = "knowledge_bases_only"
    SESSION_AND_KNOWLEDGE_BASES = "session_and_knowledge_bases"


class AnalysisJobStatus(TextEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DLPAction(TextEnum):
    ALLOW = "allow"
    MASK = "mask"
    REDACT = "redact"
    BLOCK = "block"


class RiskLevel(TextEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INSUFFICIENT = "insufficient"


class ErrorCode(TextEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    DOCUMENT_NOT_READY = "DOCUMENT_NOT_READY"
    ATTACHMENT_NOT_READY = "ATTACHMENT_NOT_READY"
    ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    DLP_BLOCKED = "DLP_BLOCKED"
    EMBEDDING_SERVICE_ERROR = "EMBEDDING_SERVICE_ERROR"
    LLM_SERVICE_ERROR = "LLM_SERVICE_ERROR"
    CHAT_BUSY = "CHAT_BUSY"
    CHAT_TIMEOUT = "CHAT_TIMEOUT"
    PROMPT_TOO_LARGE = "PROMPT_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
