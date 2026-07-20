from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.schemas.skill import (
    SkillCreate,
    SkillDetail,
    SkillFileInfo,
    SkillFileMutationResponse,
    SkillListResponse,
    SkillSummary,
    SkillUpdate,
)
from app.services.skill_service import SkillFileRecord, SkillRecord, SkillService

router = APIRouter()


@router.get("", response_model=SkillListResponse)
async def list_skills(
    principal: Principal = Depends(get_current_principal),
) -> SkillListResponse:
    service = SkillService()
    return SkillListResponse(
        items=[_summary(service, item) for item in service.list_skills(principal)]
    )


@router.post("", response_model=SkillDetail, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillCreate,
    principal: Principal = Depends(get_current_principal),
) -> SkillDetail:
    _ensure_admin(principal)
    service = SkillService()
    return _detail(
        service,
        service.create(
            name=payload.name,
            description=payload.description,
            instructions=payload.instructions,
            enabled=payload.enabled,
        ),
    )


@router.get("/{name}", response_model=SkillDetail)
async def get_skill(
    name: str,
    principal: Principal = Depends(get_current_principal),
) -> SkillDetail:
    service = SkillService()
    return _detail(service, service.get(name, principal))


@router.patch("/{name}", response_model=SkillDetail)
async def update_skill(
    name: str,
    payload: SkillUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SkillDetail:
    _ensure_admin(principal)
    service = SkillService()
    return _detail(
        service,
        service.update(
            name,
            description=payload.description,
            instructions=payload.instructions,
            enabled=payload.enabled,
        ),
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    name: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    _ensure_admin(principal)
    SkillService().delete(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{name}/files", response_model=SkillFileMutationResponse)
async def upload_skill_file(
    name: str,
    file: UploadFile = File(...),
    relative_path: str = Form(...),
    overwrite: bool = Form(default=False),
    principal: Principal = Depends(get_current_principal),
) -> SkillFileMutationResponse:
    _ensure_admin(principal)
    service = SkillService()
    record = service.save_file(
        name,
        relative_path,
        await file.read(),
        overwrite=overwrite,
    )
    return SkillFileMutationResponse(skill_name=name, file=_file_info(service, name, record))


@router.get("/{name}/files/{file_path:path}", response_class=FileResponse)
async def get_skill_file(
    name: str,
    file_path: str,
    principal: Principal = Depends(get_current_principal),
) -> FileResponse:
    record = SkillService().resolve_file(name, file_path, principal)
    return FileResponse(
        path=record.path,
        media_type=record.mime_type,
        filename=record.path.name,
        content_disposition_type="inline",
    )


@router.delete("/{name}/files/{file_path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill_file(
    name: str,
    file_path: str,
    principal: Principal = Depends(get_current_principal),
) -> Response:
    _ensure_admin(principal)
    SkillService().delete_file(name, file_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _summary(service: SkillService, record: SkillRecord) -> SkillSummary:
    return SkillSummary(
        name=record.name,
        description=record.description,
        enabled=record.enabled,
        is_system=record.is_system,
        always_on=record.always_on,
        skill_file_url=service.raw_url(record.name, "SKILL.md"),
        file_count=len(record.files),
        permission_count=len(record.permissions),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _detail(service: SkillService, record: SkillRecord) -> SkillDetail:
    return SkillDetail(
        **_summary(service, record).model_dump(),
        instructions=record.instructions,
        files=[_file_info(service, record.name, item) for item in record.files],
    )


def _file_info(service: SkillService, name: str, record: SkillFileRecord) -> SkillFileInfo:
    return SkillFileInfo(
        relative_path=record.relative_path,
        size=record.size,
        mime_type=record.mime_type,
        is_text=record.is_text,
        raw_url=service.raw_url(name, record.relative_path),
    )


def _ensure_admin(principal: Principal) -> None:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", 403)
