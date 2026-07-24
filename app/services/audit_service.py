import logging
import time
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ErrorCode, MessageRole, RiskLevel
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage, ChatSession
from app.models.masking import MaskingEvent
from app.models.user import User
from app.repositories.audit_repository import (
    AuditEventRepository,
    ChatAuditRepository,
    LLMCallLogRepository,
    MaskingEventRepository,
    RetrievalLogRepository,
)
from app.repositories.chat_repository import ChatMessageRepository
from app.repositories.user_repository import UserRepository
from app.security.dlp.base import DLPResult
from app.services.vector_store_service import RetrievedChunk
from app.utils.token_counter import count_tokens

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def record_event(
        self,
        event_type: str,
        message: str,
        metadata: dict | None = None,
        *,
        user_id: UUID | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        risk_level: RiskLevel | str | None = None,
    ) -> AuditEvent | None:
        logger.info(
            "audit_event",
            extra={
                "event_type": event_type,
                "audit_message": message,
                "audit_metadata": metadata or {},
            },
        )
        if self.db is None:
            return None
        event = AuditEvent(
            user_id=user_id,
            event_type=event_type,
            target_type=target_type,
            target_id=target_id,
            risk_level=str(risk_level) if risk_level is not None else None,
            message=message,
            event_metadata=metadata or {},
        )
        self.db.add(event)
        return event

    def ensure_user(self, principal: Principal) -> User:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        repository = UserRepository(self.db)
        user = repository.get_by_external_user_id(principal.external_user_id)
        if user is None:
            user = User(
                external_user_id=principal.external_user_id,
                username=principal.username,
                display_name=principal.username,
                department=principal.department,
                clearance_level=principal.clearance_level.value,
                is_active=principal.is_active,
            )
            self.db.add(user)
            self.db.flush()
            return user

        user.username = principal.username
        user.department = principal.department
        user.clearance_level = principal.clearance_level.value
        user.is_active = principal.is_active
        user.updated_at = datetime.utcnow()
        self.db.flush()
        return user

    def ensure_session(
        self,
        *,
        principal: Principal,
        user: User,
        session_id: UUID | None,
        title_seed: str,
    ) -> ChatSession:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)

        session = self.db.get(ChatSession, session_id) if session_id is not None else None
        if session is not None:
            if session.user_id != user.id and "admin" not in principal.roles:
                self.record_event(
                    "permission_denied",
                    "User attempted to access another user's chat session.",
                    {"session_id": str(session.id)},
                    user_id=user.id,
                    target_type="chat_session",
                    target_id=session.id,
                    risk_level=RiskLevel.MEDIUM,
                )
                self.db.commit()
                raise APIError(ErrorCode.PERMISSION_DENIED, "User does not have permission to access this session.", 403)
            session.updated_at = datetime.utcnow()
            self.db.flush()
            return session

        session = ChatSession(
            id=session_id,
            user_id=user.id,
            title=self._title_from_query(title_seed),
        ) if session_id is not None else ChatSession(
            user_id=user.id,
            title=self._title_from_query(title_seed),
        )
        self.db.add(session)
        self.db.flush()
        return session

    def record_message(
        self,
        *,
        session: ChatSession,
        user_id: UUID | None,
        role: MessageRole,
        original_content: str | None,
        masked_content: str | None,
        final_content: str | None,
        risk_level: RiskLevel | str | None,
    ) -> ChatMessage:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        message = ChatMessage(
            session_id=session.id,
            user_id=user_id,
            role=role.value,
            original_content=original_content,
            masked_content=masked_content,
            final_content=final_content,
            risk_level=str(risk_level) if risk_level is not None else None,
        )
        self.db.add(message)
        session.updated_at = datetime.utcnow()
        self.db.flush()
        return message

    def record_masking_events(self, message_id: UUID, result: DLPResult, location: str) -> None:
        if self.db is None:
            return
        for finding, masked_entity in zip(result.findings, result.masked_entities, strict=False):
            self.db.add(
                MaskingEvent(
                    message_id=message_id,
                    entity_type=finding.entity_type,
                    original_value=None,
                    masked_value=masked_entity.masked_value,
                    masking_method=finding.action.value,
                    confidence=finding.confidence,
                    risk_level=finding.risk_level.value,
                    location=location,
                )
            )

    def record_retrieval_logs(
        self,
        *,
        message_id: UUID,
        query: str,
        chunks: list[RetrievedChunk],
        used_chunk_ids: set[UUID],
    ) -> None:
        if self.db is None:
            return
        for rank, chunk in enumerate(chunks, start=1):
            self.db.add(
                RetrievalLog(
                    message_id=message_id,
                    query=query,
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    vector_score=chunk.vector_score,
                    keyword_score=chunk.keyword_score,
                    rerank_score=None,
                    final_score=chunk.final_score,
                    rank=rank,
                    used_in_context=chunk.chunk_id in used_chunk_ids,
                )
            )

    async def record_llm_call(
        self,
        *,
        message_id: UUID,
        system_prompt: str,
        user_prompt: str,
        operation,
    ) -> tuple[str, int]:
        started = time.perf_counter()
        try:
            answer = await operation()
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._add_llm_log(
                message_id=message_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                answer="",
                latency_ms=latency_ms,
                status="failed",
                error_message=str(exc),
            )
            self.record_event(
                "llm_call_failed",
                "LLM call failed.",
                {"error": str(exc)},
                target_type="chat_message",
                target_id=message_id,
                risk_level=RiskLevel.HIGH,
            )
            if self.db is not None:
                self.db.commit()
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        self._add_llm_log(
            message_id=message_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            answer=answer,
            latency_ms=latency_ms,
            status="success",
            error_message=None,
        )
        return answer, latency_ms

    def record_completed_llm_call(
        self,
        *,
        message_id: UUID,
        system_prompt: str,
        user_prompt: str,
        answer: str,
        latency_ms: int,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        self._add_llm_log(
            message_id=message_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            answer=answer,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )

    def list_chat_messages(self, session_id: UUID) -> list[ChatMessage]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return ChatMessageRepository(self.db).list_for_session(session_id)

    def list_chat_logs(
        self,
        *,
        user_id: str | None,
        start_time,
        end_time,
        risk_level: str | None,
        limit: int = 100,
    ) -> list[ChatMessage]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return ChatAuditRepository(self.db).list_chat_logs(
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            risk_level=risk_level,
            limit=limit,
        )

    def list_masking_events(self, limit: int = 100) -> list[MaskingEvent]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return MaskingEventRepository(self.db).list_recent(limit=limit)

    def list_retrieval_logs(
        self,
        *,
        message_id: UUID | None = None,
        document_id: UUID | None = None,
        limit: int = 100,
    ) -> list[RetrievalLog]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return RetrievalLogRepository(self.db).list_recent(
            message_id=message_id,
            document_id=document_id,
            limit=limit,
        )

    def list_llm_logs(
        self,
        *,
        message_id: UUID | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[LLMCallLog]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return LLMCallLogRepository(self.db).list_recent(
            message_id=message_id,
            status=status,
            limit=limit,
        )

    def list_permission_denied_events(
        self,
        *,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        return AuditEventRepository(self.db).list_recent(
            event_type="permission_denied",
            user_id=user_id,
            limit=limit,
        )

    def _add_llm_log(
        self,
        *,
        message_id: UUID,
        system_prompt: str,
        user_prompt: str,
        answer: str,
        latency_ms: int,
        status: str,
        error_message: str | None,
    ) -> None:
        if self.db is None:
            return
        prompt_tokens = count_tokens(system_prompt) + count_tokens(user_prompt)
        completion_tokens = count_tokens(answer)
        self.db.add(
            LLMCallLog(
                message_id=message_id,
                model_name=settings.llm_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                latency_ms=latency_ms,
                status=status,
                error_message=error_message,
            )
        )

    def _title_from_query(self, query: str) -> str:
        title = " ".join(query.strip().split())
        if not title:
            return "Untitled chat"
        return title[:80]
