from uuid import UUID

from pydantic import BaseModel

from app.core.constants import PermissionLevel, PermissionSubjectType


class DocumentPermissionCreate(BaseModel):
    subject_type: PermissionSubjectType
    subject_value: str
    permission: PermissionLevel


class DocumentPermissionResponse(DocumentPermissionCreate):
    permission_id: UUID | None = None
    document_id: UUID


class DocumentPermissionsResponse(BaseModel):
    document_id: UUID
    permissions: list[DocumentPermissionResponse]


class KnowledgeBasePermissionCreate(BaseModel):
    subject_type: PermissionSubjectType
    subject_value: str
    permission: PermissionLevel


class KnowledgeBasePermissionResponse(KnowledgeBasePermissionCreate):
    permission_id: UUID | None = None
    knowledge_base_id: UUID


class KnowledgeBasePermissionsResponse(BaseModel):
    knowledge_base_id: UUID
    permissions: list[KnowledgeBasePermissionResponse]
