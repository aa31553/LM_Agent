from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.workspace import Workspace
from app.schemas.workspace import (
    AnalysisArtifactListResponse,
    AnalysisArtifactResponse,
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspacePermissionCreate,
    WorkspacePermissionResponse,
    WorkspacePermissionsResponse,
    WorkspaceResponse,
    WorkspaceSessionLinkResponse,
    WorkspaceUpdate,
)
from app.services.analysis_artifact_service import AnalysisArtifactService
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage

router = APIRouter()


@router.post("", response_model=WorkspaceResponse, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    service = WorkspaceService(db)
    return _workspace_response(
        service,
        service.create(payload, principal),
        principal,
    )


@router.get("", response_model=WorkspaceListResponse)
def list_workspaces(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspaceListResponse:
    service = WorkspaceService(db)
    return WorkspaceListResponse(
        items=[
            _workspace_response(service, workspace, principal)
            for workspace in service.list_accessible(principal)
        ]
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    service = WorkspaceService(db)
    workspace = service.get(workspace_id, principal)
    return _workspace_response(service, workspace, principal)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    service = WorkspaceService(db)
    workspace = service.update(workspace_id, payload, principal)
    return _workspace_response(service, workspace, principal)


@router.delete(
    "/{workspace_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_workspace(
    workspace_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    WorkspaceService(db).delete(workspace_id, principal)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@router.put(
    "/{workspace_id}/sessions/{session_id}",
    response_model=WorkspaceSessionLinkResponse,
)
def link_workspace_session(
    workspace_id: UUID,
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspaceSessionLinkResponse:
    WorkspaceService(db).link_session(workspace_id, session_id, principal)
    return WorkspaceSessionLinkResponse(
        workspace_id=workspace_id,
        session_id=session_id,
        linked=True,
    )


@router.delete(
    "/{workspace_id}/sessions/{session_id}",
    response_model=WorkspaceSessionLinkResponse,
)
def unlink_workspace_session(
    workspace_id: UUID,
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspaceSessionLinkResponse:
    WorkspaceService(db).unlink_session(workspace_id, session_id, principal)
    return WorkspaceSessionLinkResponse(
        workspace_id=workspace_id,
        session_id=session_id,
        linked=False,
    )


@router.post(
    "/{workspace_id}/permissions",
    response_model=WorkspacePermissionResponse,
)
def set_workspace_permission(
    workspace_id: UUID,
    payload: WorkspacePermissionCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspacePermissionResponse:
    permission = WorkspaceService(db).create_or_update_permission(
        workspace_id,
        subject_type=payload.subject_type,
        subject_value=payload.subject_value,
        permission=payload.permission,
        principal=principal,
    )
    return _permission_response(permission)


@router.get(
    "/{workspace_id}/permissions",
    response_model=WorkspacePermissionsResponse,
)
def list_workspace_permissions(
    workspace_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> WorkspacePermissionsResponse:
    permissions = WorkspaceService(db).list_permissions(workspace_id, principal)
    return WorkspacePermissionsResponse(
        workspace_id=workspace_id,
        permissions=[_permission_response(item) for item in permissions],
    )


@router.delete(
    "/{workspace_id}/permissions/{permission_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_workspace_permission(
    workspace_id: UUID,
    permission_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    WorkspaceService(db).delete_permission(
        workspace_id,
        permission_id,
        principal,
    )
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@router.get(
    "/{workspace_id}/artifacts",
    response_model=AnalysisArtifactListResponse,
)
def list_workspace_artifacts(
    workspace_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisArtifactListResponse:
    artifacts = WorkspaceService(db).list_artifacts(workspace_id, principal)
    return AnalysisArtifactListResponse(
        workspace_id=workspace_id,
        items=[_artifact_response(item) for item in artifacts],
    )


@router.get(
    "/{workspace_id}/artifacts/{artifact_id}",
    response_model=AnalysisArtifactResponse,
)
def get_workspace_artifact(
    workspace_id: UUID,
    artifact_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisArtifactResponse:
    artifact = AnalysisArtifactService(db).get(artifact_id, principal)
    if artifact.workspace_id != workspace_id:
        raise APIError(ErrorCode.ANALYSIS_NOT_FOUND, "Analysis artifact not found.", 404)
    return _artifact_response(artifact)


@router.get("/{workspace_id}/artifacts/{artifact_id}/download")
def download_workspace_artifact(
    workspace_id: UUID,
    artifact_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> FileResponse:
    artifact = AnalysisArtifactService(db).get(artifact_id, principal)
    if artifact.workspace_id != workspace_id:
        raise APIError(ErrorCode.ANALYSIS_NOT_FOUND, "Analysis artifact not found.", 404)
    path = WorkspaceStorage().resolve_artifact(artifact.file_path)
    if path is None:
        raise APIError(ErrorCode.ANALYSIS_NOT_FOUND, "Artifact file is unavailable.", 404)
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=artifact.filename,
    )


def _workspace_response(
    service: WorkspaceService,
    workspace: Workspace,
    principal: Principal,
) -> WorkspaceResponse:
    permission = service.permission_level(principal, workspace)
    session_count, file_count, job_count = service.counts(workspace.id)
    return WorkspaceResponse(
        workspace_id=workspace.id,
        owner_user_id=workspace.owner_user_id,
        name=workspace.name,
        description=workspace.description,
        visibility=workspace.visibility,
        is_personal=workspace.is_personal,
        is_active=workspace.is_active,
        permission=permission,
        session_count=session_count,
        file_count=file_count,
        job_count=job_count,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _permission_response(permission) -> WorkspacePermissionResponse:
    return WorkspacePermissionResponse(
        permission_id=permission.id,
        workspace_id=permission.workspace_id,
        subject_type=permission.subject_type,
        subject_value=permission.subject_value,
        permission=permission.permission,
        created_by=permission.created_by,
        created_at=permission.created_at,
        updated_at=permission.updated_at,
    )


def _artifact_response(item) -> AnalysisArtifactResponse:
    return AnalysisArtifactResponse(
        artifact_id=item.id,
        workspace_id=item.workspace_id,
        file_id=item.file_id,
        job_id=item.job_id,
        artifact_type=item.artifact_type,
        filename=item.filename,
        mime_type=item.mime_type,
        size_bytes=item.size_bytes,
        metadata=item.artifact_metadata,
        created_at=item.created_at,
    )
