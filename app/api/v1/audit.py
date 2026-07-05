from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.schemas.audit import AuditEventItem, ChatLogItem, LLMLogItem, MaskingEventItem, RetrievalLogItem
from app.services.audit_service import AuditService

router = APIRouter()


@router.get("/chat-logs", response_model=dict[str, list[ChatLogItem]])
async def get_chat_logs(
    user_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    risk_level: str | None = None,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[ChatLogItem]]:
    _ensure_admin(principal)
    items = AuditService(db).list_chat_logs(
        user_id=user_id,
        start_time=_parse_datetime(start_time, "start_time"),
        end_time=_parse_datetime(end_time, "end_time"),
        risk_level=risk_level,
    )
    return {
        "items": [
            ChatLogItem(
                message_id=item.id,
                user_id=item.user_id,
                query=item.original_content or item.final_content or "",
                risk_level=item.risk_level or "low",
                created_at=item.created_at,
            )
            for item in items
        ]
    }


@router.get("/masking-events", response_model=dict[str, list[MaskingEventItem]])
async def get_masking_events(
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[MaskingEventItem]]:
    _ensure_admin(principal)
    items = AuditService(db).list_masking_events(limit=limit)
    return {
        "items": [
            MaskingEventItem(
                event_id=item.id,
                message_id=item.message_id,
                entity_type=item.entity_type,
                masked_value=item.masked_value,
                masking_method=item.masking_method,
                risk_level=item.risk_level,
                created_at=item.created_at,
            )
            for item in items
        ]
    }


@router.get("/retrieval-logs", response_model=dict[str, list[RetrievalLogItem]])
async def get_retrieval_logs(
    message_id: UUID | None = None,
    document_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[RetrievalLogItem]]:
    _ensure_admin(principal)
    items = AuditService(db).list_retrieval_logs(
        message_id=message_id,
        document_id=document_id,
        limit=limit,
    )
    return {
        "items": [
            RetrievalLogItem(
                log_id=item.id,
                message_id=item.message_id,
                query=item.query,
                document_id=item.document_id,
                chunk_id=item.chunk_id,
                vector_score=item.vector_score,
                keyword_score=item.keyword_score,
                rerank_score=item.rerank_score,
                final_score=item.final_score,
                rank=item.rank,
                used_in_context=item.used_in_context,
                created_at=item.created_at,
            )
            for item in items
        ]
    }


@router.get("/llm-logs", response_model=dict[str, list[LLMLogItem]])
async def get_llm_logs(
    message_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[LLMLogItem]]:
    _ensure_admin(principal)
    items = AuditService(db).list_llm_logs(
        message_id=message_id,
        status=status,
        limit=limit,
    )
    return {
        "items": [
            LLMLogItem(
                log_id=item.id,
                message_id=item.message_id,
                model_name=item.model_name,
                prompt_tokens=item.prompt_tokens,
                completion_tokens=item.completion_tokens,
                total_tokens=item.total_tokens,
                latency_ms=item.latency_ms,
                status=item.status,
                error_message=item.error_message,
                created_at=item.created_at,
            )
            for item in items
        ]
    }


@router.get("/permission-denied", response_model=dict[str, list[AuditEventItem]])
async def get_permission_denied_events(
    user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[AuditEventItem]]:
    _ensure_admin(principal)
    items = AuditService(db).list_permission_denied_events(user_id=user_id, limit=limit)
    return {
        "items": [
            AuditEventItem(
                event_id=item.id,
                user_id=item.user_id,
                event_type=item.event_type,
                target_type=item.target_type,
                target_id=item.target_id,
                risk_level=item.risk_level,
                message=item.message,
                metadata=item.event_metadata,
                created_at=item.created_at,
            )
            for item in items
        ]
    }


def _ensure_admin(principal: Principal) -> None:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)


def _parse_datetime(value: str | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name} must be an ISO 8601 datetime.",
            status_code=400,
        ) from exc
