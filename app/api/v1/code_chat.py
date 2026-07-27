import json

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.schemas.chat import CodeChatRequest, CodeChatResponse
from app.services.code_chat_service import CodeChatService

router = APIRouter()


@router.post("/query", response_model=CodeChatResponse)
async def query(
    payload: CodeChatRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CodeChatResponse:
    return await CodeChatService(db=db).answer(payload, request_id=request_id, principal=principal)


@router.post("/stream")
async def stream_query(
    payload: CodeChatRequest,
    request_id: str = Header(default="", alias="X-Request-ID"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    async def event_generator():
        async for event in CodeChatService(db=db).stream_answer(
            payload,
            request_id=request_id,
            principal=principal,
        ):
            response = event.get("response")
            serializable = dict(event)
            if response is not None and hasattr(response, "model_dump"):
                serializable["response"] = response.model_dump(mode="json")
            yield f"event: {serializable.get('event', 'message')}\\ndata: {json.dumps(serializable, ensure_ascii=False)}\\n\\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
