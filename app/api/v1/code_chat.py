import asyncio
import json

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.constants import RetrievalScope
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.rag.scoped_retriever import ScopedHybridRetriever
from app.schemas.chat import CodeChatRequest, CodeChatResponse
from app.services.chat_runtime_service import chat_runtime_service
from app.services.code_chat_service import CodeChatService

router = APIRouter()


@router.post("/query", response_model=CodeChatResponse)
async def query(
    payload: CodeChatRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CodeChatResponse:
    async with chat_runtime_service.request_slot():
        return await _code_chat_service(db, payload, principal).answer(
            payload,
            request_id=request_id,
            principal=principal,
        )


@router.post("/stream")
async def stream_query(
    payload: CodeChatRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    async def event_generator():
        try:
            async with chat_runtime_service.request_slot():
                async for event in _code_chat_service(
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
            yield _sse_event(
                {
                    "event": "error",
                    "request_id": request_id,
                    "error_code": exc.error_code.value,
                    "message": exc.message,
                    "status_code": exc.status_code,
                    "details": exc.details,
                }
            )
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


def _code_chat_service(
    db: Session,
    payload: CodeChatRequest,
    principal: Principal,
) -> CodeChatService:
    service = CodeChatService(
        db=db,
        model=payload.model,
        thinking_mode=payload.thinking_mode,
    )
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
        service.rag_service.retriever = retriever
    return service


def _sse_event(event: dict) -> str:
    response = event.get("response")
    serializable = dict(event)
    if response is not None and hasattr(response, "model_dump"):
        serializable["response"] = response.model_dump(mode="json")
    return (
        f"event: {serializable.get('event', 'message')}\n"
        f"data: {json.dumps(serializable, ensure_ascii=False)}\n\n"
    )
