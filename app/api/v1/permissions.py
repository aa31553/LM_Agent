from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.schemas.permission import (
    DocumentPermissionCreate,
    DocumentPermissionResponse,
    DocumentPermissionsResponse,
    KnowledgeBasePermissionCreate,
    KnowledgeBasePermissionResponse,
    KnowledgeBasePermissionsResponse,
    SkillPermissionCreate,
    SkillPermissionResponse,
    SkillPermissionsResponse,
)
from app.services.permission_service import PermissionService
from app.services.skill_service import SkillPermissionRecord, SkillService

router = APIRouter()


@router.post("/documents/{document_id}", response_model=DocumentPermissionResponse)
async def set_document_permission(
    document_id: UUID,
    payload: DocumentPermissionCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentPermissionResponse:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    permission = PermissionService(db).create_or_update_document_permission(
        document_id=document_id,
        subject_type=payload.subject_type,
        subject_value=payload.subject_value,
        permission=payload.permission,
    )
    return DocumentPermissionResponse(
        permission_id=permission.id,
        document_id=permission.document_id,
        subject_type=permission.subject_type,
        subject_value=permission.subject_value,
        permission=permission.permission,
    )


@router.get("/documents/{document_id}", response_model=DocumentPermissionsResponse)
async def list_document_permissions(
    document_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentPermissionsResponse:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    permissions = PermissionService(db).list_document_permissions(document_id)
    return DocumentPermissionsResponse(
        document_id=document_id,
        permissions=[
            DocumentPermissionResponse(
                permission_id=permission.id,
                document_id=permission.document_id,
                subject_type=permission.subject_type,
                subject_value=permission.subject_value,
                permission=permission.permission,
            )
            for permission in permissions
        ],
    )


@router.delete("/documents/permissions/{permission_id}", status_code=204)
async def delete_document_permission(
    permission_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    PermissionService(db).delete_document_permission(permission_id)


@router.post("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBasePermissionResponse)
async def set_knowledge_base_permission(
    knowledge_base_id: UUID,
    payload: KnowledgeBasePermissionCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> KnowledgeBasePermissionResponse:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    permission = PermissionService(db).create_or_update_knowledge_base_permission(
        knowledge_base_id=knowledge_base_id,
        subject_type=payload.subject_type,
        subject_value=payload.subject_value,
        permission=payload.permission,
    )
    return KnowledgeBasePermissionResponse(
        permission_id=permission.id,
        knowledge_base_id=permission.knowledge_base_id,
        subject_type=permission.subject_type,
        subject_value=permission.subject_value,
        permission=permission.permission,
    )


@router.get("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBasePermissionsResponse)
async def list_knowledge_base_permissions(
    knowledge_base_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> KnowledgeBasePermissionsResponse:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    permissions = PermissionService(db).list_knowledge_base_permissions(knowledge_base_id)
    return KnowledgeBasePermissionsResponse(
        knowledge_base_id=knowledge_base_id,
        permissions=[
            KnowledgeBasePermissionResponse(
                permission_id=permission.id,
                knowledge_base_id=permission.knowledge_base_id,
                subject_type=permission.subject_type,
                subject_value=permission.subject_value,
                permission=permission.permission,
            )
            for permission in permissions
        ],
    )


@router.delete("/knowledge-bases/permissions/{permission_id}", status_code=204)
async def delete_knowledge_base_permission(
    permission_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    PermissionService(db).delete_knowledge_base_permission(permission_id)


@router.post("/skills/{skill_name}", response_model=SkillPermissionResponse)
async def set_skill_permission(
    skill_name: str,
    payload: SkillPermissionCreate,
    principal: Principal = Depends(get_current_principal),
) -> SkillPermissionResponse:
    _ensure_admin(principal)
    record = SkillService().create_or_update_permission(
        skill_name,
        subject_type=payload.subject_type,
        subject_value=payload.subject_value,
        permission=payload.permission,
    )
    return _skill_permission_response(skill_name, record)


@router.get("/skills/{skill_name}", response_model=SkillPermissionsResponse)
async def list_skill_permissions(
    skill_name: str,
    principal: Principal = Depends(get_current_principal),
) -> SkillPermissionsResponse:
    _ensure_admin(principal)
    records = SkillService().list_permissions(skill_name)
    return SkillPermissionsResponse(
        skill_name=skill_name,
        permissions=[_skill_permission_response(skill_name, item) for item in records],
    )


@router.delete("/skills/{skill_name}/{permission_id}", status_code=204)
async def delete_skill_permission(
    skill_name: str,
    permission_id: UUID,
    principal: Principal = Depends(get_current_principal),
) -> None:
    _ensure_admin(principal)
    SkillService().delete_permission(skill_name, permission_id)


def _skill_permission_response(
    skill_name: str,
    record: SkillPermissionRecord,
) -> SkillPermissionResponse:
    return SkillPermissionResponse(
        permission_id=record.permission_id,
        skill_name=skill_name,
        subject_type=record.subject_type,
        subject_value=record.subject_value,
        permission=record.permission,
    )


def _ensure_admin(principal: Principal) -> None:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
