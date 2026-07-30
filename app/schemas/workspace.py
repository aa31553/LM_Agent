from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.constants import (
    PermissionLevel,
    PermissionSubjectType,
    WorkspaceVisibility,
)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    visibility: WorkspaceVisibility = WorkspaceVisibility.PRIVATE


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    visibility: WorkspaceVisibility | None = None
    is_active: bool | None = None


class WorkspaceResponse(BaseModel):
    workspace_id: UUID
    owner_user_id: UUID
    name: str
    description: str | None = None
    visibility: WorkspaceVisibility
    is_personal: bool
    is_active: bool
    permission: PermissionLevel
    session_count: int = 0
    file_count: int = 0
    job_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceResponse]


class WorkspacePermissionCreate(BaseModel):
    subject_type: PermissionSubjectType
    subject_value: str = Field(min_length=1, max_length=255)
    permission: PermissionLevel


class WorkspacePermissionResponse(WorkspacePermissionCreate):
    permission_id: UUID
    workspace_id: UUID
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class WorkspacePermissionsResponse(BaseModel):
    workspace_id: UUID
    permissions: list[WorkspacePermissionResponse]


class WorkspaceSessionLinkResponse(BaseModel):
    workspace_id: UUID
    session_id: UUID
    linked: bool


class AnalysisArtifactResponse(BaseModel):
    artifact_id: UUID
    workspace_id: UUID
    file_id: UUID | None = None
    job_id: UUID | None = None
    artifact_type: str
    filename: str
    mime_type: str | None = None
    size_bytes: int
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class AnalysisArtifactListResponse(BaseModel):
    workspace_id: UUID
    items: list[AnalysisArtifactResponse]
