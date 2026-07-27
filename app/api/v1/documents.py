from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.core.constants import ChatType, ConfidentialLevel, DocumentScope, DocumentStatus, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.repositories.document_repository import DocumentRepository
from app.schemas.common import PageResponse
from app.schemas.document import (
    DocumentArchiveResponse,
    DocumentDetail,
    DocumentFormatItem,
    DocumentFormatsResponse,
    DocumentListItem,
    DocumentStatusResponse,
    DocumentUpdate,
    DocumentUploadResponse,
    ReindexResponse,
)
from app.services.document_ingestion_service import DocumentIngestionService
from app.services.permission_service import PermissionService
from app.services.processing_queue_service import ProcessingQueueService
from app.utils.file_utils import supported_upload_formats

router = APIRouter()


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    scope: DocumentScope = Form(default=DocumentScope.KNOWLEDGE_BASE),
    knowledge_base_id: UUID | None = Form(default=None),
    session_id: UUID | None = Form(default=None),
    chat_type: ChatType = Form(default=ChatType.GENERAL),
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
        scope=scope,
        knowledge_base_id=knowledge_base_id,
        session_id=session_id,
        chat_type=chat_type,
        confidential_level=confidential_level,
        department=department,
        document_type=document_type,
        version=version,
        request_id=request_id,
        principal=principal,
    )


@router.get("/formats", response_model=DocumentFormatsResponse)
async def list_document_formats(
    principal: Principal = Depends(get_current_principal),
) -> DocumentFormatsResponse:
    del principal
    formats = supported_upload_formats()
    return DocumentFormatsResponse(
        items=[DocumentFormatItem(**item) for item in formats],
        accept=",".join(item["extension"] for item in formats),
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
    if knowledge_base_id is not None:
        knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Knowledge base not found.", 404)
        permission_service.ensure_knowledge_base_read(principal, knowledge_base)
    documents = repository.list_all(
        knowledge_base_id=knowledge_base_id,
        status=status,
        confidential_level=confidential_level.value if confidential_level else None,
    )
    allowed_documents = [
        document for document in documents if permission_service.can_read_document(principal, document)
    ]
    total = len(allowed_documents)
    start = (page - 1) * page_size
    items = [
        DocumentIngestionService(db=db).list_item(document)
        for document in allowed_documents[start : start + page_size]
    ]
    return PageResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
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


@router.patch("/{document_id}", response_model=DocumentDetail)
async def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    service = DocumentIngestionService(db=db)
    document = service.get_document_model(document_id)
    permission_service = PermissionService(db)
    permission_service.ensure_document_write(principal, document)
    if payload.confidential_level is not None:
        permission_service.ensure_level_access(principal, payload.confidential_level)
    return service.detail_from_document(service.update_metadata(document_id, payload))


@router.post("/{document_id}/reindex", response_model=ReindexResponse)
async def reindex_document(
    document_id: UUID,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ReindexResponse:
    document = DocumentIngestionService(db=db).get_document_model(document_id)
    PermissionService(db).ensure_document_write(principal, document)
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
    service = DocumentIngestionService(db=db)
    document = service.get_document_model(document_id)
    PermissionService(db).ensure_document_write(principal, document)
    return service.archive(document_id)


@router.delete("/{document_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    service = DocumentIngestionService(db=db)
    document = service.get_document_model(document_id)
    PermissionService(db).ensure_document_manage(principal, document)
    service.delete(document_id)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
