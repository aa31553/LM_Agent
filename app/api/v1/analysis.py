from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.schemas.analysis_recipe import (
    AnalysisIntentValidateRequest,
    AnalysisIntentValidationResponse,
    RecipeDefinition,
    RecipeListResponse,
    RecipeMetricsResponse,
)
from app.services.analysis_artifact_service import AnalysisArtifactService
from app.services.analysis_metrics_service import AnalysisMetricsService
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler
from app.services.analysis_recipe_registry import AnalysisRecipeRegistry
from app.services.chat_runtime_service import chat_runtime_service
from app.services.dataset_query_service import DatasetQueryService
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi import status as http_status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnalysisFileStatus,
    AnalysisJobStatus,
    ChatType,
    ConfidentialLevel,
    ErrorCode,
    PermissionLevel,
    ThinkingMode,
)
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.chat import ChatSession
from app.models.workspace import AnalysisArtifact
from app.schemas.analysis import (
    AnalysisArtifactCreatedResponse,
    AnalysisChartArtifactRequest,
    AnalysisExplanationRequest,
    AnalysisExplanationResponse,
    AnalysisExportRequest,
    AnalysisFileItem,
    AnalysisFileListResponse,
    AnalysisFileUploadResponse,
    AnalysisHybridRequest,
    AnalysisHybridResponse,
    AnalysisJobCreate,
    AnalysisJobListResponse,
    AnalysisJobResponse,
    AnalysisPlanDraftConfirmRequest,
    AnalysisPlanDraftCreate,
    AnalysisPlanDraftResponse,
    AnalysisPlanValidateRequest,
    AnalysisPlanValidationResponse,
    AnalysisReportRequest,
    SpreadsheetInspectionResponse,
)
from app.services.analysis_job_service import AnalysisJobService
from app.services.analysis_orchestrator_service import AnalysisOrchestratorService
from app.services.audit_service import AuditService
from app.services.hybrid_analysis_service import HybridAnalysisService
from app.services.permission_service import PermissionService
from app.services.spreadsheet_analysis_service import SpreadsheetAnalysisService
from app.services.spreadsheet_profile_job_service import SpreadsheetProfileJobService
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage
from app.utils.file_utils import infer_file_type, is_supported_upload
from app.utils.llm_usage import get_llm_token_usage, reset_llm_token_usage

router = APIRouter()


def _representation_fields(source: AnalysisFile) -> dict:
    manifest = source.dataset_manifest or {}
    datasets = manifest.get("datasets") or []
    return {
        "representation_format": manifest.get("representation_format"),
        "llm_readable": bool(manifest.get("representation_path")),
        "analysis_ready": bool(datasets),
    }


def _ensure_recipes_enabled() -> None:
    if not settings.analysis_recipes_enabled:
        raise APIError(
            ErrorCode.RECIPE_NOT_FOUND,
            "Analysis recipes are disabled by the rollout feature flag.",
            503,
        )


@router.get("/recipes", response_model=RecipeListResponse)
def list_analysis_recipes(
    _principal: Principal = Depends(get_current_principal),
) -> RecipeListResponse:
    _ensure_recipes_enabled()
    return RecipeListResponse(items=AnalysisRecipeRegistry().list_enabled())


@router.get("/recipes/{recipe_id}", response_model=RecipeDefinition)
def get_analysis_recipe(
    recipe_id: str,
    version: str = Query(default="1.0", min_length=1, max_length=16),
    _principal: Principal = Depends(get_current_principal),
) -> RecipeDefinition:
    _ensure_recipes_enabled()
    return AnalysisRecipeRegistry().get(recipe_id, version)


@router.post(
    "/intents/validate",
    response_model=AnalysisIntentValidationResponse,
)
def validate_analysis_intent(
    payload: AnalysisIntentValidateRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisIntentValidationResponse:
    _ensure_recipes_enabled()
    workspace = WorkspaceService(db).get(
        payload.workspace_id,
        principal,
        PermissionLevel.READ,
    )
    sources = [
        AnalysisJobService(db).get_analysis_file(
            source.file_id,
            principal,
            required=PermissionLevel.READ,
        )
        for source in payload.intent.sources
    ]
    if any(source.workspace_id != workspace.id for source in sources):
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "All intent sources must belong to the requested workspace.",
            400,
        )
    not_ready = [
        str(source.id)
        for source in sources
        if source.status != AnalysisFileStatus.READY.value or not source.dataset_manifest
    ]
    if not_ready:
        raise APIError(
            ErrorCode.DOCUMENT_NOT_READY,
            "All intent sources must finish preprocessing.",
            409,
            details={"file_ids": not_ready},
        )
    manifests = {str(source.id): source.dataset_manifest for source in sources}
    normalized, plan, actions, warnings = AnalysisRecipeCompiler().compile(
        payload.intent,
        manifests,
        allowed_file_ids=set(manifests),
    )
    return AnalysisIntentValidationResponse(
        normalized_intent=normalized,
        executable_plan=plan,
        normalization_actions=actions,
        warnings=warnings,
    )


@router.get("/metrics/recipes", response_model=RecipeMetricsResponse)
def get_analysis_recipe_metrics(
    workspace_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> RecipeMetricsResponse:
    return AnalysisMetricsService(db).summarize(workspace_id, principal)


@router.post("/files/upload", response_model=AnalysisFileUploadResponse)
async def upload_analysis_file(
    file: UploadFile = File(...),
    session_id: UUID | None = Form(default=None),
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
    if not is_supported_upload(file.filename):
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "Unsupported workspace file type. Use the same formats accepted by document upload.",
            400,
        )
    PermissionService(db).ensure_level_access(principal, confidential_level)

    audit_service = AuditService(db)
    user = audit_service.ensure_user(principal)
    if session_id is None:
        if workspace_id is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "workspace_id is required when session_id is omitted.",
                400,
            )
        session = None
        workspace = WorkspaceService(db).get(
            workspace_id,
            principal,
            PermissionLevel.WRITE,
        )
    else:
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
            max_bytes=(
                settings.analysis_upload_max_bytes
                if file_type in SpreadsheetAnalysisService.SUPPORTED_TYPES
                else settings.document_max_upload_bytes
            ),
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
        session_id=session.id if session is not None else None,
        created_by=user.id,
        filename=stored_filename,
        original_filename=file.filename,
        file_type=file_type,
        file_path=file_path,
        size_bytes=size_bytes,
        confidential_level=confidential_level.value,
        status=AnalysisFileStatus.PROFILE_QUEUED.value,
        profile_progress=0,
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
                "uploaded_from_session_id": (str(session.id) if session is not None else None),
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
        "A workspace file was uploaded for LLM-readable preprocessing.",
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
        session_id=session.id if session is not None else None,
        filename=file.filename,
        file_type=file_type,
        size_bytes=size_bytes,
        status=source.status,
        profile_progress=source.profile_progress,
        expires_at=None,
        **_representation_fields(source),
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
                profile_progress=source.profile_progress,
                profile_error=source.profile_error,
                dataset_count=len((source.dataset_manifest or {}).get("datasets", [])),
                profiled_at=source.profiled_at,
                expires_at=source.expires_at,
                created_at=source.created_at,
                **_representation_fields(source),
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
    if not (source.dataset_manifest or {}).get("datasets"):
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "This workspace file is LLM-readable but is not a deterministic spreadsheet dataset.",
            409,
        )
    storage = WorkspaceStorage()
    cached = storage.load_json(source.profile_path) if source.profile_path is not None else None
    if cached is not None and isinstance(cached.get("sheets"), list):
        sheets = cached["sheets"]
        warnings = cached.get("warnings", [])
    elif source.status == AnalysisFileStatus.READY.value:
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
    else:
        raise APIError(
            ErrorCode.DOCUMENT_NOT_READY,
            "Spreadsheet profiling is not complete.",
            409,
            details={
                "status": source.status,
                "progress": source.profile_progress,
                "error": source.profile_error,
            },
        )
    return SpreadsheetInspectionResponse(
        file_id=source.id,
        workspace_id=source.workspace_id,
        filename=source.original_filename,
        file_type=source.file_type,
        sheets=sheets,
        warnings=warnings,
    )


@router.get("/files/{file_id}", response_model=AnalysisFileItem)
def get_analysis_file(
    file_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisFileItem:
    source = AnalysisJobService(db).get_analysis_file(file_id, principal)
    return AnalysisFileItem(
        file_id=source.id,
        workspace_id=source.workspace_id,
        session_id=source.session_id,
        filename=source.original_filename,
        file_type=source.file_type,
        size_bytes=source.size_bytes,
        status=source.status,
        profile_progress=source.profile_progress,
        profile_error=source.profile_error,
        dataset_count=len((source.dataset_manifest or {}).get("datasets", [])),
        profiled_at=source.profiled_at,
        expires_at=source.expires_at,
        created_at=source.created_at,
        **_representation_fields(source),
    )


@router.post(
    "/files/{file_id}/profile/retry",
    response_model=AnalysisFileUploadResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_spreadsheet_profile(
    file_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisFileUploadResponse:
    source = SpreadsheetProfileJobService(db).retry(file_id, principal)
    return AnalysisFileUploadResponse(
        file_id=source.id,
        workspace_id=source.workspace_id,
        session_id=source.session_id,
        filename=source.original_filename,
        file_type=source.file_type,
        size_bytes=source.size_bytes,
        status=source.status,
        profile_progress=source.profile_progress,
        profile_error=source.profile_error,
        dataset_count=0,
        profiled_at=source.profiled_at,
        expires_at=source.expires_at,
        **_representation_fields(source),
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
    if payload.plan.sources:
        sources = [
            AnalysisJobService(db).get_analysis_file(item.file_id, principal)
            for item in payload.plan.sources
        ]
        if any(item.workspace_id != source.workspace_id for item in sources):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "All analysis sources must belong to the same workspace.",
                400,
            )
        manifests = {str(item.id): item.dataset_manifest for item in sources}
        normalized, warnings = DatasetQueryService().validate_plan(
            payload.plan,
            manifests,
        )
    else:
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


@router.post(
    "/plan-drafts",
    response_model=AnalysisPlanDraftResponse,
    status_code=http_status.HTTP_201_CREATED,
)
@router.post(
    "/intent-drafts",
    response_model=AnalysisPlanDraftResponse,
    status_code=http_status.HTTP_201_CREATED,
    include_in_schema=True,
)
async def create_analysis_plan_draft(
    payload: AnalysisPlanDraftCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisPlanDraftResponse:
    reset_llm_token_usage()
    service = AnalysisOrchestratorService(
        db,
        model=payload.model,
        thinking_mode=payload.thinking_mode,
    )
    async with chat_runtime_service.request_slot():
        draft = await service.create_draft(payload, principal)
    raw_usage = get_llm_token_usage()
    response = service.response(draft)
    response.usage.prompt_tokens = raw_usage.prompt_tokens
    response.usage.completion_tokens = raw_usage.completion_tokens
    response.usage.total_tokens = raw_usage.total_tokens
    return response


@router.get(
    "/plan-drafts/{draft_id}",
    response_model=AnalysisPlanDraftResponse,
)
@router.get(
    "/intent-drafts/{draft_id}",
    response_model=AnalysisPlanDraftResponse,
)
def get_analysis_plan_draft(
    draft_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisPlanDraftResponse:
    service = AnalysisOrchestratorService(db)
    return service.response(service.get(draft_id, principal))


@router.post(
    "/plan-drafts/{draft_id}/confirm",
    response_model=AnalysisJobResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
@router.post(
    "/intent-drafts/{draft_id}/confirm",
    response_model=AnalysisJobResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def confirm_analysis_plan_draft(
    draft_id: UUID,
    payload: AnalysisPlanDraftConfirmRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisJobResponse:
    job = AnalysisOrchestratorService(db).confirm(
        draft_id,
        principal,
        edited_plan=payload.plan,
        clarification_choice=payload.clarification_choice,
    )
    return AnalysisJobService(db).response(job)


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
    payload: AnalysisExplanationRequest | None = None,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisExplanationResponse:
    service = AnalysisJobService(db)
    job = service.get(job_id, principal)
    reset_llm_token_usage()
    async with chat_runtime_service.request_slot():
        answer = await service.explain(
            job,
            model=payload.model if payload is not None else None,
            thinking_mode=(
                payload.thinking_mode if payload is not None else ThinkingMode.DEFAULT
            ),
            use_tools=payload.use_tools if payload is not None else False,
            principal=principal,
        )
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


@router.post(
    "/hybrid-answer",
    response_model=AnalysisHybridResponse,
)
async def hybrid_analysis_answer(
    payload: AnalysisHybridRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisHybridResponse:
    reset_llm_token_usage()
    async with chat_runtime_service.request_slot():
        answer, citations, report = await HybridAnalysisService(
            db,
            model=payload.model,
            thinking_mode=payload.thinking_mode,
        ).answer(
            payload,
            principal,
        )
    raw_usage = get_llm_token_usage()
    return AnalysisHybridResponse(
        workspace_id=payload.workspace_id,
        answer=answer,
        analysis_job_ids=payload.analysis_job_ids,
        citations=citations,
        report_artifact_id=report.id if report is not None else None,
        usage={
            "prompt_tokens": raw_usage.prompt_tokens,
            "completion_tokens": raw_usage.completion_tokens,
            "total_tokens": raw_usage.total_tokens,
        },
    )


@router.post(
    "/jobs/{job_id}/export",
    response_model=AnalysisArtifactCreatedResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def export_analysis_job(
    job_id: UUID,
    payload: AnalysisExportRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisArtifactCreatedResponse:
    return _artifact_created(AnalysisArtifactService(db).export(job_id, payload, principal))


@router.post(
    "/jobs/{job_id}/reports",
    response_model=AnalysisArtifactCreatedResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def create_analysis_report(
    job_id: UUID,
    payload: AnalysisReportRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisArtifactCreatedResponse:
    return _artifact_created(AnalysisArtifactService(db).create_report(job_id, payload, principal))


@router.post(
    "/jobs/{job_id}/charts",
    response_model=AnalysisArtifactCreatedResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def create_analysis_chart_artifact(
    job_id: UUID,
    payload: AnalysisChartArtifactRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AnalysisArtifactCreatedResponse:
    return _artifact_created(AnalysisArtifactService(db).create_chart(job_id, payload, principal))


def _artifact_created(artifact: AnalysisArtifact) -> AnalysisArtifactCreatedResponse:
    return AnalysisArtifactCreatedResponse(
        artifact_id=artifact.id,
        workspace_id=artifact.workspace_id,
        job_id=artifact.job_id,
        artifact_type=artifact.artifact_type,
        filename=artifact.filename,
        mime_type=artifact.mime_type or "application/octet-stream",
        size_bytes=artifact.size_bytes,
    )
