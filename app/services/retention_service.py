from datetime import datetime, timedelta

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.constants import DocumentStatus
from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage
from app.models.document import Document
from app.models.masking import MaskingEvent
from app.schemas.admin import RetentionPolicyRequest, RetentionPolicyResponse


class RetentionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def enforce(self, policy: RetentionPolicyRequest) -> RetentionPolicyResponse:
        now = datetime.utcnow()
        documents_archived = self._archive_documents(
            cutoff=now - timedelta(days=policy.document_archive_after_days),
            apply=policy.apply,
        )
        retrieval_deleted = self._delete_old_rows(
            RetrievalLog,
            now - timedelta(days=policy.retrieval_log_retention_days),
            policy.apply,
        )
        masking_deleted = self._delete_old_rows(
            MaskingEvent,
            now - timedelta(days=policy.masking_event_retention_days),
            policy.apply,
        )
        llm_deleted = self._delete_old_rows(
            LLMCallLog,
            now - timedelta(days=policy.llm_log_retention_days),
            policy.apply,
        )
        audit_deleted = self._delete_old_rows(
            AuditEvent,
            now - timedelta(days=policy.audit_event_retention_days),
            policy.apply,
        )
        chat_deleted = self._delete_old_chat_messages(
            cutoff=now - timedelta(days=policy.chat_message_retention_days),
            apply=policy.apply,
        )
        if policy.apply:
            self.db.commit()
        return RetentionPolicyResponse(
            applied=policy.apply,
            documents_archived=documents_archived,
            chat_messages_deleted=chat_deleted,
            retrieval_logs_deleted=retrieval_deleted,
            masking_events_deleted=masking_deleted,
            llm_logs_deleted=llm_deleted,
            audit_events_deleted=audit_deleted,
        )

    def _archive_documents(self, cutoff: datetime, apply: bool) -> int:
        query = self.db.query(Document).filter(
            Document.created_at < cutoff,
            Document.status != DocumentStatus.ARCHIVED.value,
        )
        count = query.count()
        if apply and count:
            query.update(
                {
                    Document.status: DocumentStatus.ARCHIVED.value,
                    Document.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        return int(count)

    def _delete_old_rows(self, model, cutoff: datetime, apply: bool) -> int:
        count = int(self.db.scalar(select(func.count()).where(model.created_at < cutoff)) or 0)
        if apply and count:
            self.db.execute(delete(model).where(model.created_at < cutoff))
        return count

    def _delete_old_chat_messages(self, cutoff: datetime, apply: bool) -> int:
        blocked_by_retrieval = exists().where(RetrievalLog.message_id == ChatMessage.id)
        blocked_by_masking = exists().where(MaskingEvent.message_id == ChatMessage.id)
        blocked_by_llm = exists().where(LLMCallLog.message_id == ChatMessage.id)
        criteria = (
            ChatMessage.created_at < cutoff,
            ~blocked_by_retrieval,
            ~blocked_by_masking,
            ~blocked_by_llm,
        )
        count = int(self.db.scalar(select(func.count()).where(*criteria)) or 0)
        if apply and count:
            self.db.execute(delete(ChatMessage).where(*criteria))
        return count
