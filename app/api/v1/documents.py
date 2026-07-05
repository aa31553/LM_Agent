from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.constants import ConfidentialLevel, DocumentStatus, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.repositories.document_repository import DocumentRepository
from app.schemas.common import PageResponse
from app.schemas.document import (
    DocumentArchiveResponse,
    DocumentDetail,
    DocumentListItem,
    DocumentStatusResponse,
    DocumentUploadResponse,
    ReindexResponse,
)
from app.services.document_ingestion_service import DocumentIngestionService
from app.services.permission_service import PermissionService
from app.services.processing_queue_service import ProcessingQueueService

router = APIRouter()


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    knowledge_base_id: UUID = Form(...),
    confidential_level: ConfidentialLevel = Form(...),
    department: str | None = Form(default=None),
    document_type: str | None = Form(default=None),
    version: str | None = Form(default=None),
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentUploadResponse:
    return await DocumentIngestionService(db=db).queue_upload(
        file=file,
        knowledge_base_id=knowledge_base_id,
        confidential_level=confidential_level,
        department=department,
        document_type=document_type,
        version=version,
        request_id=request_id,
        principal=principal,
    )


@router.get("", response_model=PageResponse[DocumentListItem])
async def list_documents(
    knowledge_base_id: UUID | None = None,
    status: str | None = None,
    confidential_level: ConfidentialLevel | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> PageResponse[DocumentListItem]:
    repository = DocumentRepository(db)
    permission_service = PermissionService(db)
    documents = repository.list(
        knowledge_base_id=knowledge_base_id,
        status=status,
        confidential_level=confidential_level.value if confidential_level else None,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    allowed_documents = [
        document for document in documents if permission_service.can_read_document(principal, document)
    ]
    items = [DocumentIngestionService(db=db).list_item(document) for document in allowed_documents]
    return PageResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=len(allowed_documents),
    )


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentStatusResponse:
    service = DocumentIngestionService(db=db)
    document = service.get_document_model(document_id)
    PermissionService(db).ensure_document_read(principal, document)
    return service.status(document_id)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    service = DocumentIngestionService(db=db)
    document = service.get_document_model(document_id)
    PermissionService(db).ensure_document_read(principal, document)
    return service.detail_from_document(document)


@router.post("/{document_id}/reindex", response_model=ReindexResponse)
async def reindex_document(
    document_id: UUID,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ReindexResponse:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    document = DocumentIngestionService(db=db).get_document_model(document_id)
    if document.status == DocumentStatus.ARCHIVED.value:
        raise APIError(ErrorCode.INVALID_REQUEST, "Archived documents cannot be reindexed.", 400)
    if not Path(document.file_path).exists():
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "Original document file is missing and cannot be reindexed.",
            400,
        )
    job = ProcessingQueueService(db).enqueue(document_id, "document")
    return ReindexResponse(
        request_id=request_id,
        document_id=document_id,
        status=job.status,
        message=(
            "Reindex task has been queued."
            if job.status == "queued"
            else "Reindex task is already running."
        ),
    )


@router.post("/{document_id}/archive", response_model=DocumentArchiveResponse)
async def archive_document(
    document_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentArchiveResponse:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)
    return DocumentIngestionService(db=db).archive(document_id)
