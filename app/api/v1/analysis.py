from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi import status as http_status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ChatType, ConfidentialLevel, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.schemas.analysis import (
    AnalysisExplanationResponse,
    AnalysisFileItem,
    AnalysisFileListResponse,
    AnalysisFileUploadResponse,
    AnalysisJobCreate,
    AnalysisJobResponse,
    AnalysisPlanValidateRequest,
    AnalysisPlanValidationResponse,
    SpreadsheetInspectionResponse,
)
from app.services.analysis_job_service import AnalysisJobService
from app.services.audit_service import AuditService
from app.services.chat_runtime_service import chat_runtime_service
from app.services.permission_service import PermissionService
from app.services.spreadsheet_analysis_service import SpreadsheetAnalysisService
from app.storage.local_storage import LocalStorage
from app.utils.file_utils import infer_file_type
from app.utils.llm_usage import get_llm_token_usage, reset_llm_token_usage

router = APIRouter()


@router.post("/files/upload", response_model=AnalysisFileUploadResponse)
async def upload_analysis_file(
    file: UploadFile = File(...),
    session_id: UUID = Form(...),
    confidential_level: ConfidentialLevel = Form(
        default=ConfidentialLevel.INTERNAL
    ),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisFileUploadResponse:
    if not file.filename:
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "A file name is required.",
            400,
        )
    file_type = infer_file_type(file.filename)
    if file_type not in SpreadsheetAnalysisService.SUPPORTED_TYPES:
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "The analysis workspace accepts only .xlsx and .csv files.",
            400,
        )
    PermissionService(db).ensure_level_access(principal, confidential_level)

    audit_service = AuditService(db)
    user = audit_service.ensure_user(principal)
    session = audit_service.ensure_session(
        principal=principal,
        user=user,
        session_id=session_id,
        title_seed=file.filename,
        chat_type=ChatType.GENERAL,
    )
    file_id = uuid4()
    stored_filename = f"{file_id}-{Path(file.filename).name}"
    try:
        file_path, size_bytes = await LocalStorage().save_upload(
            "analysis",
            stored_filename,
            file,
            max_bytes=settings.analysis_upload_max_bytes,
        )
    except ValueError as exc:
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            str(exc),
            413,
        ) from exc

    expires_at = datetime.utcnow() + timedelta(
        hours=settings.analysis_retention_hours
    )
    source = AnalysisFile(
        id=file_id,
        session_id=session.id,
        created_by=user.id,
        filename=stored_filename,
        original_filename=file.filename,
        file_type=file_type,
        file_path=file_path,
        size_bytes=size_bytes,
        confidential_level=confidential_level.value,
        status="ready",
        expires_at=expires_at,
    )
    db.add(source)
    audit_service.record_event(
        "analysis_file_uploaded",
        "A session-scoped spreadsheet was uploaded for deterministic analysis.",
        {"filename": file.filename, "size_bytes": size_bytes},
        user_id=user.id,
        target_type="analysis_file",
        target_id=source.id,
        risk_level="low",
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        LocalStorage().delete_artifact(file_path)
        raise
    return AnalysisFileUploadResponse(
        file_id=source.id,
        session_id=session.id,
        filename=file.filename,
        file_type=file_type,
        size_bytes=size_bytes,
        expires_at=expires_at,
    )


@router.get("/files", response_model=AnalysisFileListResponse)
def list_analysis_files(
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisFileListResponse:
    user = AuditService(db).ensure_user(principal)
    session = db.get(ChatSession, session_id)
    if session is None:
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "Chat session not found.",
            404,
        )
    if session.user_id != user.id and "admin" not in principal.roles:
        raise APIError(
            ErrorCode.PERMISSION_DENIED,
            "User does not have permission to access this session.",
            403,
        )
    sources = list(
        db.scalars(
            select(AnalysisFile)
            .where(AnalysisFile.session_id == session.id)
            .order_by(AnalysisFile.created_at.desc())
        )
    )
    return AnalysisFileListResponse(
        session_id=session.id,
        items=[
            AnalysisFileItem(
                file_id=source.id,
                session_id=source.session_id,
                filename=source.original_filename,
                file_type=source.file_type,
                size_bytes=source.size_bytes,
                status=source.status,
                expires_at=source.expires_at,
                created_at=source.created_at,
            )
            for source in sources
            if source.expires_at is None
            or source.expires_at > datetime.utcnow()
        ],
    )


@router.get(
    "/files/{file_id}/inspect",
    response_model=SpreadsheetInspectionResponse,
)
def inspect_analysis_file(
    file_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SpreadsheetInspectionResponse:
    source = AnalysisJobService(db).get_analysis_file(file_id, principal)
    sheets = SpreadsheetAnalysisService().inspect(
        source.file_path,
        source.file_type,
    )
    return SpreadsheetInspectionResponse(
        file_id=source.id,
        filename=source.original_filename,
        file_type=source.file_type,
        sheets=sheets,
    )


@router.delete(
    "/files/{file_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_analysis_file(
    file_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    source = AnalysisJobService(db).get_analysis_file(file_id, principal)
    db.execute(delete(AnalysisJob).where(AnalysisJob.file_id == source.id))
    db.delete(source)
    db.commit()
    LocalStorage().delete_artifact(source.file_path)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post(
    "/plans/validate",
    response_model=AnalysisPlanValidationResponse,
)
def validate_analysis_plan(
    payload: AnalysisPlanValidateRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisPlanValidationResponse:
    source = AnalysisJobService(db).get_analysis_file(
        payload.file_id,
        principal,
    )
    normalized, warnings = SpreadsheetAnalysisService().validate_plan(
        source.file_path,
        source.file_type,
        payload.plan,
    )
    return AnalysisPlanValidationResponse(
        normalized_plan=normalized,
        warnings=warnings,
    )


@router.post("/jobs", response_model=AnalysisJobResponse, status_code=202)
def create_analysis_job(
    payload: AnalysisJobCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobResponse:
    service = AnalysisJobService(db)
    job = service.create(payload, principal)
    return service.response(job)


@router.get("/jobs/{job_id}", response_model=AnalysisJobResponse)
def get_analysis_job(
    job_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobResponse:
    service = AnalysisJobService(db)
    return service.response(service.get(job_id, principal))


@router.post(
    "/jobs/{job_id}/explain",
    response_model=AnalysisExplanationResponse,
)
async def explain_analysis_job(
    job_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisExplanationResponse:
    service = AnalysisJobService(db)
    job = service.get(job_id, principal)
    reset_llm_token_usage()
    async with chat_runtime_service.request_slot():
        answer = await service.explain(job)
    raw_usage = get_llm_token_usage()
    return AnalysisExplanationResponse(
        job_id=job.id,
        answer=answer,
        usage={
            "prompt_tokens": raw_usage.prompt_tokens,
            "completion_tokens": raw_usage.completion_tokens,
            "total_tokens": raw_usage.total_tokens,
        },
    )
