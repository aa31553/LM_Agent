from uuid import UUID

from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage
from app.models.masking import MaskingEvent
from app.models.user import User
from app.repositories.base import BaseRepository


class AuditEventRepository(BaseRepository[AuditEvent]):
    model = AuditEvent

    def list_recent(
        self,
        *,
        event_type: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        query = self.db.query(AuditEvent)
        if event_type:
            query = query.filter(AuditEvent.event_type == event_type)
        if user_id:
            try:
                parsed_user_id = UUID(user_id)
            except ValueError:
                query = query.join(User, User.id == AuditEvent.user_id)
                query = query.filter(User.external_user_id == user_id)
            else:
                query = query.filter(AuditEvent.user_id == parsed_user_id)
        return query.order_by(AuditEvent.created_at.desc()).limit(limit).all()


class RetrievalLogRepository(BaseRepository[RetrievalLog]):
    model = RetrievalLog

    def list_for_message(self, message_id: object) -> list[RetrievalLog]:
        return (
            self.db.query(RetrievalLog)
            .filter(RetrievalLog.message_id == message_id)
            .order_by(RetrievalLog.rank.asc())
            .all()
        )

    def list_recent(
        self,
        *,
        message_id: object | None = None,
        document_id: object | None = None,
        limit: int = 100,
    ) -> list[RetrievalLog]:
        query = self.db.query(RetrievalLog)
        if message_id is not None:
            query = query.filter(RetrievalLog.message_id == message_id)
        if document_id is not None:
            query = query.filter(RetrievalLog.document_id == document_id)
        return query.order_by(RetrievalLog.created_at.desc(), RetrievalLog.rank.asc()).limit(limit).all()


class MaskingEventRepository(BaseRepository[MaskingEvent]):
    model = MaskingEvent

    def list_recent(self, limit: int = 100) -> list[MaskingEvent]:
        return (
            self.db.query(MaskingEvent)
            .order_by(MaskingEvent.created_at.desc())
            .limit(limit)
            .all()
        )


class LLMCallLogRepository(BaseRepository[LLMCallLog]):
    model = LLMCallLog

    def list_for_message(self, message_id: object) -> list[LLMCallLog]:
        return (
            self.db.query(LLMCallLog)
            .filter(LLMCallLog.message_id == message_id)
            .order_by(LLMCallLog.created_at.desc())
            .all()
        )

    def list_recent(
        self,
        *,
        message_id: object | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[LLMCallLog]:
        query = self.db.query(LLMCallLog)
        if message_id is not None:
            query = query.filter(LLMCallLog.message_id == message_id)
        if status is not None:
            query = query.filter(LLMCallLog.status == status)
        return query.order_by(LLMCallLog.created_at.desc()).limit(limit).all()


class ChatAuditRepository:
    def __init__(self, db):
        self.db = db

    def list_chat_logs(
        self,
        user_id: str | None = None,
        start_time=None,
        end_time=None,
        risk_level: str | None = None,
        limit: int = 100,
    ) -> list[ChatMessage]:
        query = self.db.query(ChatMessage).filter(ChatMessage.role == "user")
        if user_id:
            query = query.join(User, User.id == ChatMessage.user_id)
            try:
                parsed_user_id = UUID(user_id)
            except ValueError:
                query = query.filter(User.external_user_id == user_id)
            else:
                query = query.filter((User.id == parsed_user_id) | (User.external_user_id == user_id))
        if start_time is not None:
            query = query.filter(ChatMessage.created_at >= start_time)
        if end_time is not None:
            query = query.filter(ChatMessage.created_at <= end_time)
        if risk_level:
            query = query.filter(ChatMessage.risk_level == risk_level)
        return query.order_by(ChatMessage.created_at.desc()).limit(limit).all()
