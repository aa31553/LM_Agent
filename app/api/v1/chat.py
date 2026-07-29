import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import ErrorCode, RetrievalScope
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.chat import ChatSession
from app.models.document import Document
from app.rag.scoped_retriever import ScopedHybridRetriever
from app.schemas.chat import ChatMessage as ChatMessageSchema
from app.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    ChatSessionDeleteResponse,
    ChatSessionMessages,
    SessionAttachmentDeleteResponse,
    SessionAttachmentItem,
    SessionAttachmentListResponse,
)
from app.services.audit_service import AuditService
from app.services.chat_runtime_service import chat_runtime_service
from app.services.document_ingestion_service import DocumentIngestionService
from app.services.permission_service import PermissionService
from app.services.rag_service import RAGService
from app.services.session_service import SessionService

router = APIRouter()


@router.post("/query", response_model=ChatQueryResponse)
async def query(
    payload: ChatQueryRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatQueryResponse:
    async with chat_runtime_service.request_slot():
        return await _rag_service(db, payload, principal).answer(
            payload,
            request_id=request_id,
            principal=principal,
        )


@router.post("/stream")
async def stream_query(
    payload: ChatQueryRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    async def event_generator():
        try:
            async with chat_runtime_service.request_slot():
                async for event in _rag_service(
                    db,
                    payload,
                    principal,
                ).stream_answer(
                    payload,
                    request_id=request_id,
                    principal=principal,
                ):
                    yield _sse_event(event)
        except APIError as exc:
            db.rollback()
            yield _sse_event(_error_event(exc, request_id))
        except asyncio.CancelledError:
            db.rollback()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _rag_service(
    db: Session,
    payload: ChatQueryRequest,
    principal: Principal,
) -> RAGService:
    service = RAGService(db=db)
    if payload.attachment_ids or payload.retrieval_scope != RetrievalScope.AUTO:
        retriever = ScopedHybridRetriever(
            db=db,
            attachment_ids=payload.attachment_ids,
            retrieval_scope=payload.retrieval_scope,
        )
        retriever.validate_request(
            session_id=payload.session_id,
            principal=principal,
        )
        service.retriever = retriever
    return service


def _sse_event(event: dict) -> str:
    name = str(event.get("event", "message"))
    payload = dict(event)
    response = payload.get("response")
    if response is not None and hasattr(response, "model_dump"):
        payload["response"] = response.model_dump(mode="json")
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_event(exc: APIError, request_id: str) -> dict:
    return {
        "event": "error",
        "request_id": request_id,
        "error_code": exc.error_code.value,
        "message": exc.message,
        "status_code": exc.status_code,
        "details": exc.details,
    }


@router.delete("/sessions/{session_id}", response_model=ChatSessionDeleteResponse)
async def delete_session(
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatSessionDeleteResponse:
    return SessionService(db).delete(session_id, principal)


@router.get("/sessions/{session_id}/messages", response_model=ChatSessionMessages)
async def get_session_messages(
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatSessionMessages:
    _ensure_session_access(db, principal, session_id)
    audit_service = AuditService(db)
    messages = audit_service.list_chat_messages(session_id)
    return ChatSessionMessages(
        session_id=session_id,
        messages=[
            ChatMessageSchema(
                message_id=message.id,
                role=message.role,
                content=(
                    message.final_content
                    or message.masked_content
                    or message.original_content
                    or ""
                ),
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.get(
    "/sessions/{session_id}/attachments",
    response_model=SessionAttachmentListResponse,
)
def list_session_attachments(
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SessionAttachmentListResponse:
    _ensure_session_access(db, principal, session_id)
    documents = list(
        db.scalars(
            select(Document)
            .where(Document.session_id == session_id)
            .order_by(Document.created_at.desc())
        )
    )
    return SessionAttachmentListResponse(
        session_id=session_id,
        items=[_attachment_item(document) for document in documents],
    )


@router.delete(
    "/sessions/{session_id}/attachments/{document_id}",
    response_model=SessionAttachmentDeleteResponse,
)
def delete_session_attachment(
    session_id: UUID,
    document_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SessionAttachmentDeleteResponse:
    _ensure_session_access(db, principal, session_id)
    document = db.get(Document, document_id)
    if document is None or document.session_id != session_id:
        raise APIError(
            ErrorCode.DOCUMENT_NOT_FOUND,
            "Session attachment not found.",
            404,
        )
    PermissionService(db).ensure_document_write(principal, document)
    deleted_files = DocumentIngestionService(db=db).delete(document.id)
    return SessionAttachmentDeleteResponse(
        session_id=session_id,
        document_id=document_id,
        deleted_files=deleted_files,
    )


def _ensure_session_access(
    db: Session,
    principal: Principal,
    session_id: UUID,
) -> ChatSession:
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
    return session


def _attachment_item(document: Document) -> SessionAttachmentItem:
    return SessionAttachmentItem(
        document_id=document.id,
        filename=document.original_filename or document.filename,
        file_type=document.file_type,
        status=document.status,
        source_type=document.source_type,
        page_count=document.page_count,
        chunk_count=document.chunk_count,
        created_at=document.created_at,
    )
