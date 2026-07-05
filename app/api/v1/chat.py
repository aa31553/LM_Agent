import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.models.chat import ChatSession
from app.schemas.chat import ChatQueryRequest, ChatQueryResponse, ChatSessionMessages
from app.schemas.chat import ChatMessage as ChatMessageSchema
from app.services.audit_service import AuditService
from app.services.rag_service import RAGService

router = APIRouter()


@router.post("/query", response_model=ChatQueryResponse)
async def query(
    payload: ChatQueryRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatQueryResponse:
    return await RAGService(db=db).answer(payload, request_id=request_id, principal=principal)


@router.post("/stream")
async def stream_query(
    payload: ChatQueryRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    async def event_generator():
        async for event in RAGService(db=db).stream_answer(
            payload,
            request_id=request_id,
            principal=principal,
        ):
            yield _sse_event(event)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _sse_event(event: dict) -> str:
    name = str(event.get("event", "message"))
    payload = dict(event)
    response = payload.get("response")
    if response is not None and hasattr(response, "model_dump"):
        payload["response"] = response.model_dump(mode="json")
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/sessions/{session_id}/messages", response_model=ChatSessionMessages)
async def get_session_messages(
    session_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatSessionMessages:
    audit_service = AuditService(db)
    user = audit_service.ensure_user(principal)
    session = db.get(ChatSession, session_id)
    if session is None:
        raise APIError(ErrorCode.INVALID_REQUEST, "Chat session not found.", status_code=404)
    if session.user_id != user.id and "admin" not in principal.roles:
        raise APIError(
            ErrorCode.PERMISSION_DENIED,
            "User does not have permission to access this session.",
            status_code=403,
        )
    messages = audit_service.list_chat_messages(session_id)
    return ChatSessionMessages(
        session_id=session_id,
        messages=[
            ChatMessageSchema(
                message_id=message.id,
                role=message.role,
                content=message.final_content or message.masked_content or message.original_content or "",
                created_at=message.created_at,
            )
            for message in messages
        ],
    )
