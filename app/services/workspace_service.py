from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.constants import (
    ErrorCode,
    PermissionLevel,
    PermissionSubjectType,
    WorkspaceVisibility,
)
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.models.workspace import AnalysisArtifact, Workspace, WorkspacePermission
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate
from app.services.audit_service import AuditService
from app.storage.workspace_storage import WorkspaceStorage

_ALLOWED_LEVELS = {
    PermissionLevel.READ: {
        PermissionLevel.READ.value,
        PermissionLevel.WRITE.value,
        PermissionLevel.ADMIN.value,
    },
    PermissionLevel.WRITE: {
        PermissionLevel.WRITE.value,
        PermissionLevel.ADMIN.value,
    },
    PermissionLevel.ADMIN: {PermissionLevel.ADMIN.value},
}


class WorkspaceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        payload: WorkspaceCreate,
        principal: Principal,
    ) -> Workspace:
        user = AuditService(self.db).ensure_user(principal)
        workspace = Workspace(
            owner_user_id=user.id,
            name=payload.name.strip(),
            description=payload.description,
            visibility=payload.visibility.value,
            is_personal=False,
            is_active=True,
        )
        self.db.add(workspace)
        self.db.flush()
        self._record(
            "workspace_created",
            workspace,
            user.id,
            {"name": workspace.name},
        )
        self.db.commit()
        self.db.refresh(workspace)
        return workspace

    def get_or_create_personal(self, principal: Principal) -> Workspace:
        user = AuditService(self.db).ensure_user(principal)
        workspace = self.db.scalar(
            select(Workspace)
            .where(
                Workspace.owner_user_id == user.id,
                Workspace.is_personal.is_(True),
                Workspace.is_active.is_(True),
            )
            .order_by(Workspace.created_at.asc())
            .limit(1)
        )
        if workspace is not None:
            return workspace
        workspace = Workspace(
            owner_user_id=user.id,
            name=f"{principal.username or principal.external_user_id} 的私人工作區",
            visibility=WorkspaceVisibility.PRIVATE.value,
            is_personal=True,
            is_active=True,
        )
        self.db.add(workspace)
        self.db.flush()
        self._record(
            "workspace_created",
            workspace,
            user.id,
            {"personal": True},
        )
        return workspace

    def get(
        self,
        workspace_id: UUID,
        principal: Principal,
        required: PermissionLevel = PermissionLevel.READ,
    ) -> Workspace:
        workspace = self.db.get(Workspace, workspace_id)
        if workspace is None or not workspace.is_active:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Workspace not found.",
                404,
            )
        if not self.has_permission(principal, workspace, required):
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                f"Workspace {required.value} permission is required.",
                403,
            )
        return workspace

    def list_accessible(self, principal: Principal) -> list[Workspace]:
        if not principal.is_active:
            return []
        workspaces = list(
            self.db.scalars(
                select(Workspace)
                .where(Workspace.is_active.is_(True))
                .order_by(Workspace.updated_at.desc())
            )
        )
        return [
            workspace
            for workspace in workspaces
            if self.has_permission(principal, workspace, PermissionLevel.READ)
        ]

    def update(
        self,
        workspace_id: UUID,
        payload: WorkspaceUpdate,
        principal: Principal,
    ) -> Workspace:
        workspace = self.get(workspace_id, principal, PermissionLevel.ADMIN)
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("name") is None:
            changes.pop("name", None)
        if changes.get("visibility") is None:
            changes.pop("visibility", None)
        if changes.get("is_active") is None:
            changes.pop("is_active", None)
        for field, value in changes.items():
            if field == "visibility" and value is not None:
                value = value.value
            if field == "name" and value is not None:
                value = value.strip()
            setattr(workspace, field, value)
        workspace.updated_at = datetime.utcnow()
        user = AuditService(self.db).ensure_user(principal)
        self._record("workspace_updated", workspace, user.id, {"fields": sorted(changes)})
        self.db.commit()
        self.db.refresh(workspace)
        return workspace

    def delete(self, workspace_id: UUID, principal: Principal) -> None:
        workspace = self.get(workspace_id, principal, PermissionLevel.ADMIN)
        self.db.execute(
            update(ChatSession)
            .where(ChatSession.workspace_id == workspace.id)
            .values(workspace_id=None)
        )
        self.db.execute(
            delete(AnalysisArtifact).where(AnalysisArtifact.workspace_id == workspace.id)
        )
        self.db.execute(delete(AnalysisJob).where(AnalysisJob.workspace_id == workspace.id))
        self.db.execute(delete(AnalysisFile).where(AnalysisFile.workspace_id == workspace.id))
        self.db.execute(
            delete(WorkspacePermission).where(WorkspacePermission.workspace_id == workspace.id)
        )
        self.db.delete(workspace)
        self.db.commit()
        WorkspaceStorage().delete_workspace_tree(workspace_id)

    def resolve_for_session(
        self,
        session: ChatSession,
        principal: Principal,
        workspace_id: UUID | None,
        *,
        required: PermissionLevel = PermissionLevel.WRITE,
    ) -> Workspace:
        self.ensure_session_owner(session, principal)
        if workspace_id is not None:
            workspace = self.get(workspace_id, principal, required)
            if session.workspace_id not in {None, workspace.id}:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "The chat session is linked to a different workspace.",
                    409,
                )
        elif session.workspace_id is not None:
            workspace = self.get(
                session.workspace_id,
                principal,
                required,
            )
        else:
            workspace = self.get_or_create_personal(principal)
        if session.workspace_id != workspace.id:
            session.workspace_id = workspace.id
            session.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(workspace)
        return workspace

    def link_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
        principal: Principal,
    ) -> ChatSession:
        workspace = self.get(workspace_id, principal, PermissionLevel.READ)
        session = self.db.get(ChatSession, session_id)
        if session is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Chat session not found.", 404)
        self.ensure_session_owner(session, principal)
        session.workspace_id = workspace.id
        session.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(session)
        return session

    def unlink_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
        principal: Principal,
    ) -> ChatSession:
        self.get(workspace_id, principal, PermissionLevel.READ)
        session = self.db.get(ChatSession, session_id)
        if session is None or session.workspace_id != workspace_id:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The chat session is not linked to this workspace.",
                404,
            )
        self.ensure_session_owner(session, principal)
        session.workspace_id = None
        session.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(session)
        return session

    def create_or_update_permission(
        self,
        workspace_id: UUID,
        *,
        subject_type: PermissionSubjectType,
        subject_value: str,
        permission: PermissionLevel,
        principal: Principal,
    ) -> WorkspacePermission:
        workspace = self.get(workspace_id, principal, PermissionLevel.ADMIN)
        value = subject_value.strip()
        existing = self.db.scalar(
            select(WorkspacePermission).where(
                WorkspacePermission.workspace_id == workspace.id,
                WorkspacePermission.subject_type == subject_type.value,
                WorkspacePermission.subject_value == value,
            )
        )
        user = AuditService(self.db).ensure_user(principal)
        if existing is None:
            existing = WorkspacePermission(
                workspace_id=workspace.id,
                subject_type=subject_type.value,
                subject_value=value,
                permission=permission.value,
                created_by=user.id,
            )
            self.db.add(existing)
        else:
            existing.permission = permission.value
            existing.updated_at = datetime.utcnow()
        workspace.visibility = WorkspaceVisibility.SHARED.value
        workspace.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(existing)
        return existing

    def list_permissions(
        self,
        workspace_id: UUID,
        principal: Principal,
    ) -> list[WorkspacePermission]:
        workspace = self.get(workspace_id, principal, PermissionLevel.ADMIN)
        return list(
            self.db.scalars(
                select(WorkspacePermission)
                .where(WorkspacePermission.workspace_id == workspace.id)
                .order_by(WorkspacePermission.created_at.asc())
            )
        )

    def delete_permission(
        self,
        workspace_id: UUID,
        permission_id: UUID,
        principal: Principal,
    ) -> None:
        workspace = self.get(workspace_id, principal, PermissionLevel.ADMIN)
        permission = self.db.get(WorkspacePermission, permission_id)
        if permission is None or permission.workspace_id != workspace.id:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Workspace permission not found.",
                404,
            )
        self.db.delete(permission)
        self.db.commit()

    def list_artifacts(
        self,
        workspace_id: UUID,
        principal: Principal,
    ) -> list[AnalysisArtifact]:
        workspace = self.get(workspace_id, principal, PermissionLevel.READ)
        return list(
            self.db.scalars(
                select(AnalysisArtifact)
                .where(AnalysisArtifact.workspace_id == workspace.id)
                .order_by(AnalysisArtifact.created_at.desc())
            )
        )

    def permission_level(
        self,
        principal: Principal,
        workspace: Workspace,
    ) -> PermissionLevel | None:
        if not principal.is_active:
            return None
        if "admin" in principal.roles:
            return PermissionLevel.ADMIN
        user = AuditService(self.db).ensure_user(principal)
        if workspace.owner_user_id == user.id:
            return PermissionLevel.ADMIN
        permissions = list(
            self.db.scalars(
                select(WorkspacePermission).where(WorkspacePermission.workspace_id == workspace.id)
            )
        )
        levels = [
            PermissionLevel(item.permission)
            for item in permissions
            if self._subject_matches(principal, item)
        ]
        if PermissionLevel.ADMIN in levels:
            return PermissionLevel.ADMIN
        if PermissionLevel.WRITE in levels:
            return PermissionLevel.WRITE
        if PermissionLevel.READ in levels:
            return PermissionLevel.READ
        return None

    def has_permission(
        self,
        principal: Principal,
        workspace: Workspace,
        required: PermissionLevel,
    ) -> bool:
        level = self.permission_level(principal, workspace)
        return level is not None and level.value in _ALLOWED_LEVELS[required]

    def counts(self, workspace_id: UUID) -> tuple[int, int, int]:
        session_count = (
            self.db.scalar(
                select(func.count())
                .select_from(ChatSession)
                .where(ChatSession.workspace_id == workspace_id)
            )
            or 0
        )
        file_count = (
            self.db.scalar(
                select(func.count())
                .select_from(AnalysisFile)
                .where(AnalysisFile.workspace_id == workspace_id)
            )
            or 0
        )
        job_count = (
            self.db.scalar(
                select(func.count())
                .select_from(AnalysisJob)
                .where(AnalysisJob.workspace_id == workspace_id)
            )
            or 0
        )
        return session_count, file_count, job_count

    def ensure_session_owner(
        self,
        session: ChatSession,
        principal: Principal,
    ) -> None:
        user = AuditService(self.db).ensure_user(principal)
        if session.user_id != user.id and "admin" not in principal.roles:
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to access this session.",
                403,
            )

    @staticmethod
    def _subject_matches(
        principal: Principal,
        permission: WorkspacePermission,
    ) -> bool:
        if permission.subject_type == PermissionSubjectType.USER.value:
            return permission.subject_value == principal.external_user_id
        if permission.subject_type == PermissionSubjectType.DEPARTMENT.value:
            return permission.subject_value == principal.department
        if permission.subject_type == PermissionSubjectType.ROLE.value:
            return permission.subject_value in principal.roles
        if permission.subject_type == PermissionSubjectType.PROJECT.value:
            return permission.subject_value in principal.projects
        return False

    def _record(
        self,
        event_type: str,
        workspace: Workspace,
        user_id: UUID,
        metadata: dict,
    ) -> None:
        AuditService(self.db).record_event(
            event_type,
            event_type.replace("_", " ").capitalize() + ".",
            metadata,
            user_id=user_id,
            target_type="workspace",
            target_id=workspace.id,
            risk_level="low",
        )
