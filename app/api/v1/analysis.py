from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi import status as http_status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnalysisJobStatus,
    ChatType,
    ConfidentialLevel,
    ErrorCode,
    PermissionLevel,
)
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.models.workspace import AnalysisArtifact
from app.schemas.analysis import (
    AnalysisExplanationResponse,
    AnalysisFileItem,
    AnalysisFileListResponse,
    AnalysisFileUploadResponse,
    AnalysisJobCreate,
    AnalysisJobListResponse,
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
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage
from app.utils.file_utils import infer_file_type
from app.utils.llm_usage import get_llm_token_usage, reset_llm_token_usage

router = APIRouter()


@router.post("/files/upload", response_model=AnalysisFileUploadResponse)
async def upload_analysis_file(
    file: UploadFile = File(...),
    session_id: UUID = Form(...),
    workspace_id: UUID | None = Form(default=None),
    confidential_level: ConfidentialLevel = Form(default=ConfidentialLevel.INTERNAL),
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
    workspace = WorkspaceService(db).resolve_for_session(
        session,
        principal,
        workspace_id,
    )
    file_id = uuid4()
    stored_filename = Path(file.filename).name
    storage = WorkspaceStorage()
    try:
        file_path, size_bytes = await storage.save_original_upload(
            workspace.id,
            file_id,
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

    source = AnalysisFile(
        id=file_id,
        workspace_id=workspace.id,
        session_id=session.id,
        created_by=user.id,
        filename=stored_filename,
        original_filename=file.filename,
        file_type=file_type,
        file_path=file_path,
        size_bytes=size_bytes,
        confidential_level=confidential_level.value,
        status="ready",
        expires_at=None,
    )
    db.add(source)
    try:
        storage.save_file_metadata(
            workspace.id,
            source.id,
            {
                "file_id": str(source.id),
                "workspace_id": str(workspace.id),
                "uploaded_from_session_id": str(session.id),
                "original_filename": file.filename,
                "file_type": file_type,
                "size_bytes": size_bytes,
                "confidential_level": confidential_level.value,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
    except Exception:
        db.rollback()
        storage.delete_file_tree(workspace.id, file_id)
        raise
    audit_service.record_event(
        "analysis_file_uploaded",
        "A workspace spreadsheet was uploaded for deterministic analysis.",
        {
            "workspace_id": str(workspace.id),
            "filename": file.filename,
            "size_bytes": size_bytes,
        },
        user_id=user.id,
        target_type="analysis_file",
        target_id=source.id,
        risk_level="low",
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_file_tree(workspace.id, file_id)
        raise
    return AnalysisFileUploadResponse(
        file_id=source.id,
        workspace_id=workspace.id,
        session_id=session.id,
        filename=file.filename,
        file_type=file_type,
        size_bytes=size_bytes,
        expires_at=None,
    )


@router.get("/files", response_model=AnalysisFileListResponse)
def list_analysis_files(
    session_id: UUID | None = None,
    workspace_id: UUID | None = None,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisFileListResponse:
    if session_id is None and workspace_id is None:
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "session_id or workspace_id is required.",
            400,
        )
    workspace_service = WorkspaceService(db)
    session = db.get(ChatSession, session_id) if session_id is not None else None
    if session_id is not None:
        if session is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Chat session not found.", 404)
        workspace_service.ensure_session_owner(session, principal)
        workspace = workspace_service.resolve_for_session(
            session,
            principal,
            workspace_id,
            required=PermissionLevel.READ,
        )
    else:
        workspace = workspace_service.get(workspace_id, principal)
    sources = list(
        db.scalars(
            select(AnalysisFile)
            .where(AnalysisFile.workspace_id == workspace.id)
            .order_by(AnalysisFile.created_at.desc())
        )
    )
    return AnalysisFileListResponse(
        workspace_id=workspace.id,
        session_id=session.id if session is not None else None,
        items=[
            AnalysisFileItem(
                file_id=source.id,
                workspace_id=source.workspace_id,
                session_id=source.session_id,
                filename=source.original_filename,
                file_type=source.file_type,
                size_bytes=source.size_bytes,
                status=source.status,
                expires_at=source.expires_at,
                created_at=source.created_at,
            )
            for source in sources
            if source.expires_at is None or source.expires_at > datetime.utcnow()
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
    storage = WorkspaceStorage()
    cached = storage.load_json(source.profile_path) if source.profile_path is not None else None
    if cached is not None and isinstance(cached.get("sheets"), list):
        sheets = cached["sheets"]
        warnings = cached.get("warnings", [])
    else:
        sheets = SpreadsheetAnalysisService().inspect(
            source.file_path,
            source.file_type,
        )
        warnings = [warning for sheet in sheets for warning in sheet.get("warnings", [])]
        source.profile_path = storage.save_profile(
            source.workspace_id,
            source.id,
            {"sheets": sheets, "warnings": warnings},
        )
        db.commit()
    return SpreadsheetInspectionResponse(
        file_id=source.id,
        workspace_id=source.workspace_id,
        filename=source.original_filename,
        file_type=source.file_type,
        sheets=sheets,
        warnings=warnings,
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
    source = AnalysisJobService(db).get_analysis_file(
        file_id,
        principal,
        required=PermissionLevel.WRITE,
    )
    job_ids = list(db.scalars(select(AnalysisJob.id).where(AnalysisJob.file_id == source.id)))
    db.execute(delete(AnalysisArtifact).where(AnalysisArtifact.file_id == source.id))
    db.execute(delete(AnalysisJob).where(AnalysisJob.file_id == source.id))
    db.delete(source)
    db.commit()
    storage = WorkspaceStorage()
    for job_id in job_ids:
        storage.delete_job_tree(source.workspace_id, job_id)
    storage.delete_file_tree(source.workspace_id, source.id)
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


@router.get("/jobs", response_model=AnalysisJobListResponse)
def list_analysis_jobs(
    session_id: UUID | None = None,
    workspace_id: UUID | None = None,
    file_id: UUID | None = None,
    status: AnalysisJobStatus | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobListResponse:
    service = AnalysisJobService(db)
    jobs, total, resolved_workspace_id = service.list_jobs(
        principal,
        session_id=session_id,
        workspace_id=workspace_id,
        file_id=file_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return AnalysisJobListResponse(
        workspace_id=resolved_workspace_id,
        session_id=session_id,
        items=[service.response(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=AnalysisJobResponse)
def get_analysis_job(
    job_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobResponse:
    service = AnalysisJobService(db)
    return service.response(service.get(job_id, principal))


@router.post("/jobs/{job_id}/cancel", response_model=AnalysisJobResponse)
def cancel_analysis_job(
    job_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobResponse:
    service = AnalysisJobService(db)
    return service.response(service.cancel(job_id, principal))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=AnalysisJobResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_analysis_job(
    job_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobResponse:
    service = AnalysisJobService(db)
    return service.response(service.retry(job_id, principal))


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
