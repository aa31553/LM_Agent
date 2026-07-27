from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import (
    CONFIDENTIALITY_RANK,
    ConfidentialLevel,
    ErrorCode,
    PermissionLevel,
    PermissionSubjectType,
)
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.chat import ChatSession
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.permission import DocumentPermission, KnowledgeBasePermission
from app.repositories.permission_repository import (
    DocumentPermissionRepository,
    KnowledgeBasePermissionRepository,
)
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService


class PermissionService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def can_access_level(self, principal: Principal, level: ConfidentialLevel) -> bool:
        return CONFIDENTIALITY_RANK[principal.clearance_level] >= CONFIDENTIALITY_RANK[level]

    def ensure_level_access(self, principal: Principal, level: ConfidentialLevel) -> None:
        if not self.can_access_level(principal, level):
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to access this document.",
                status_code=403,
            )

    def can_read_document(self, principal: Principal, document: Document) -> bool:
        if not principal.is_active:
            return False
        if "admin" in principal.roles:
            return True
        if document.status == "archived":
            return False
        if document.session_id is not None:
            if self.db is None:
                return False
            user = UserRepository(self.db).get_by_external_user_id(principal.external_user_id)
            session = self.db.get(ChatSession, document.session_id)
            return user is not None and session is not None and session.user_id == user.id
        if self.db is None:
            return self.can_access_level(principal, ConfidentialLevel(document.confidential_level))
        if document.knowledge_base_id is None:
            return False
        knowledge_base = self.db.get(KnowledgeBase, document.knowledge_base_id)
        if knowledge_base is None or not self.can_access_knowledge_base(principal, knowledge_base):
            return False
        if not self.can_access_level(principal, ConfidentialLevel(document.confidential_level)):
            return False
        document_permissions = DocumentPermissionRepository(self.db).list_for_document(document.id)
        if document.department and principal.department == document.department:
            return True
        if document_permissions:
            return self._permissions_allow(principal, document_permissions, PermissionLevel.READ)
        return document.department is None

    def ensure_document_read(self, principal: Principal, document: Document) -> None:
        if not self.can_read_document(principal, document):
            self._record_permission_denied(principal, document)
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to access this document.",
                status_code=403,
            )

    def can_access_knowledge_base(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
    ) -> bool:
        if not principal.is_active:
            return False
        if "admin" in principal.roles:
            return True
        if not knowledge_base.is_active:
            return False
        if self.db is not None:
            user = UserRepository(self.db).get_by_external_user_id(principal.external_user_id)
            if user is not None and knowledge_base.created_by == user.id:
                return True
        if knowledge_base.owner_department and principal.department == knowledge_base.owner_department:
            return True
        if self.db is None:
            return True
        permissions = KnowledgeBasePermissionRepository(self.db).list_for_knowledge_base(
            knowledge_base.id
        )
        if not permissions:
            return True
        return self._permissions_allow(principal, permissions, PermissionLevel.READ)

    def ensure_knowledge_base_read(self, principal: Principal, knowledge_base: KnowledgeBase) -> None:
        if not self.can_access_knowledge_base(principal, knowledge_base):
            raise APIError(ErrorCode.PERMISSION_DENIED, "User does not have access to this knowledge base.", 403)

    def can_write_knowledge_base(self, principal: Principal, knowledge_base: KnowledgeBase) -> bool:
        if not principal.is_active:
            return False
        if "admin" in principal.roles:
            return True
        if self.db is None:
            return False
        user = UserRepository(self.db).get_by_external_user_id(principal.external_user_id)
        if user is not None and knowledge_base.created_by == user.id:
            return True
        permissions = KnowledgeBasePermissionRepository(self.db).list_for_knowledge_base(
            knowledge_base.id
        )
        return self._permissions_allow(principal, permissions, PermissionLevel.WRITE)

    def ensure_knowledge_base_write(self, principal: Principal, knowledge_base: KnowledgeBase) -> None:
        if not self.can_write_knowledge_base(principal, knowledge_base):
            raise APIError(ErrorCode.PERMISSION_DENIED, "Write permission is required for this knowledge base.", 403)

    def can_manage_knowledge_base(self, principal: Principal, knowledge_base: KnowledgeBase) -> bool:
        if not principal.is_active:
            return False
        if "admin" in principal.roles:
            return True
        if self.db is None:
            return False
        user = UserRepository(self.db).get_by_external_user_id(principal.external_user_id)
        if user is not None and knowledge_base.created_by == user.id:
            return True
        permissions = KnowledgeBasePermissionRepository(self.db).list_for_knowledge_base(
            knowledge_base.id
        )
        return self._permissions_allow(principal, permissions, PermissionLevel.ADMIN)

    def ensure_knowledge_base_manage(self, principal: Principal, knowledge_base: KnowledgeBase) -> None:
        if not self.can_manage_knowledge_base(principal, knowledge_base):
            raise APIError(ErrorCode.PERMISSION_DENIED, "Knowledge base administrator permission is required.", 403)

    def can_write_document(self, principal: Principal, document: Document) -> bool:
        if not principal.is_active:
            return False
        if "admin" in principal.roles:
            return True
        if self.db is None:
            return False
        user = UserRepository(self.db).get_by_external_user_id(principal.external_user_id)
        if user is not None and document.created_by == user.id:
            return True
        if document.session_id is not None:
            session = self.db.get(ChatSession, document.session_id)
            return user is not None and session is not None and session.user_id == user.id
        if document.knowledge_base_id is None:
            return False
        knowledge_base = self.db.get(KnowledgeBase, document.knowledge_base_id)
        if knowledge_base is None or not knowledge_base.is_active:
            return False
        if self.can_write_knowledge_base(principal, knowledge_base):
            return True
        return self._permissions_allow(
            principal,
            DocumentPermissionRepository(self.db).list_for_document(document.id),
            PermissionLevel.WRITE,
        )

    def ensure_document_write(self, principal: Principal, document: Document) -> None:
        if not self.can_write_document(principal, document):
            raise APIError(ErrorCode.PERMISSION_DENIED, "Write permission is required for this document.", 403)

    def can_manage_document(self, principal: Principal, document: Document) -> bool:
        if "admin" in principal.roles:
            return True
        if self.db is None or document.knowledge_base_id is None:
            return False
        knowledge_base = self.db.get(KnowledgeBase, document.knowledge_base_id)
        if knowledge_base is not None and self.can_manage_knowledge_base(principal, knowledge_base):
            return True
        return self._permissions_allow(
            principal,
            DocumentPermissionRepository(self.db).list_for_document(document.id),
            PermissionLevel.ADMIN,
        )

    def ensure_document_manage(self, principal: Principal, document: Document) -> None:
        if not self.can_manage_document(principal, document):
            raise APIError(ErrorCode.PERMISSION_DENIED, "Document administrator permission is required.", 403)

    def create_or_update_document_permission(
        self,
        document_id: UUID,
        subject_type: PermissionSubjectType,
        subject_value: str,
        permission: PermissionLevel,
    ) -> DocumentPermission:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        repository = DocumentPermissionRepository(self.db)
        existing = repository.find_existing(document_id, subject_type.value, subject_value)
        if existing is not None:
            existing.permission = permission.value
            self.db.commit()
            self.db.refresh(existing)
            return existing
        created = DocumentPermission(
            document_id=document_id,
            subject_type=subject_type.value,
            subject_value=subject_value,
            permission=permission.value,
        )
        repository.add(created)
        self.db.commit()
        self.db.refresh(created)
        return created

    def list_document_permissions(self, document_id: UUID) -> list[DocumentPermission]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return DocumentPermissionRepository(self.db).list_for_document(document_id)

    def delete_document_permission(self, permission_id: UUID) -> None:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        deleted = DocumentPermissionRepository(self.db).delete_permission(permission_id)
        self.db.commit()
        if deleted == 0:
            raise APIError(ErrorCode.INVALID_REQUEST, "Document permission not found.", 404)

    def create_or_update_knowledge_base_permission(
        self,
        knowledge_base_id: UUID,
        subject_type: PermissionSubjectType,
        subject_value: str,
        permission: PermissionLevel,
    ) -> KnowledgeBasePermission:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        if self.db.get(KnowledgeBase, knowledge_base_id) is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Knowledge base not found.", 404)
        repository = KnowledgeBasePermissionRepository(self.db)
        existing = repository.find_existing(knowledge_base_id, subject_type.value, subject_value)
        if existing is not None:
            existing.permission = permission.value
            self.db.commit()
            self.db.refresh(existing)
            return existing
        created = KnowledgeBasePermission(
            knowledge_base_id=knowledge_base_id,
            subject_type=subject_type.value,
            subject_value=subject_value,
            permission=permission.value,
        )
        repository.add(created)
        self.db.commit()
        self.db.refresh(created)
        return created

    def list_knowledge_base_permissions(self, knowledge_base_id: UUID) -> list[KnowledgeBasePermission]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return KnowledgeBasePermissionRepository(self.db).list_for_knowledge_base(knowledge_base_id)

    def delete_knowledge_base_permission(self, permission_id: UUID) -> None:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        deleted = KnowledgeBasePermissionRepository(self.db).delete_permission(permission_id)
        self.db.commit()
        if deleted == 0:
            raise APIError(ErrorCode.INVALID_REQUEST, "Knowledge base permission not found.", 404)

    def _permissions_allow(
        self,
        principal: Principal,
        permissions: list[DocumentPermission] | list[KnowledgeBasePermission],
        required: PermissionLevel,
    ) -> bool:
        allowed_levels = {
            PermissionLevel.READ: {PermissionLevel.READ.value, PermissionLevel.WRITE.value, PermissionLevel.ADMIN.value},
            PermissionLevel.WRITE: {PermissionLevel.WRITE.value, PermissionLevel.ADMIN.value},
            PermissionLevel.ADMIN: {PermissionLevel.ADMIN.value},
        }[required]
        for permission in permissions:
            if permission.permission not in allowed_levels:
                continue
            if permission.subject_type == PermissionSubjectType.USER.value:
                if permission.subject_value == principal.external_user_id:
                    return True
            elif permission.subject_type == PermissionSubjectType.DEPARTMENT.value:
                if permission.subject_value == principal.department:
                    return True
            elif permission.subject_type == PermissionSubjectType.ROLE.value:
                if permission.subject_value in principal.roles:
                    return True
        return False

    def _record_permission_denied(self, principal: Principal, document: Document) -> None:
        if self.db is None:
            return
        audit_service = AuditService(self.db)
        user = audit_service.ensure_user(principal)
        audit_service.record_event(
            "permission_denied",
            "User attempted to access a document without read permission.",
            {
                "document_id": str(document.id),
                "knowledge_base_id": str(document.knowledge_base_id),
                "document_status": document.status,
            },
            user_id=user.id,
            target_type="document",
            target_id=document.id,
            risk_level="medium",
        )
        self.db.commit()
