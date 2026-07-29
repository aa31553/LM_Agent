from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.analysis import AnalysisFile, AnalysisJob
from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document, DocumentProcessingJob
from app.models.document_chunk import DocumentChunk
from app.models.document_image import DocumentImage
from app.models.masking import MaskingEvent
from app.models.permission import DocumentPermission
from app.schemas.chat import ChatSessionDeleteResponse
from app.services.audit_service import AuditService


class SessionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def delete(
        self,
        session_id: UUID,
        principal: Principal,
    ) -> ChatSessionDeleteResponse:
        user = AuditService(self.db).ensure_user(principal)
        chat_session = self.db.get(ChatSession, session_id)
        if chat_session is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Chat session not found.",
                404,
            )
        if chat_session.user_id != user.id and "admin" not in principal.roles:
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to delete this session.",
                403,
            )

        documents = list(
            self.db.scalars(
                select(Document).where(Document.session_id == session_id)
            )
        )
        analysis_files = list(
            self.db.scalars(
                select(AnalysisFile).where(AnalysisFile.session_id == session_id)
            )
        )
        analysis_jobs = list(
            self.db.scalars(
                select(AnalysisJob).where(AnalysisJob.session_id == session_id)
            )
        )
        document_ids = [document.id for document in documents]
        analysis_file_ids = [source.id for source in analysis_files]
        analysis_job_ids = [job.id for job in analysis_jobs]
        message_ids = list(
            self.db.scalars(
                select(ChatMessage.id).where(ChatMessage.session_id == session_id)
            )
        )
        image_paths = (
            list(
                self.db.scalars(
                    select(DocumentImage.image_path).where(
                        DocumentImage.document_id.in_(document_ids)
                    )
                )
            )
            if document_ids
            else []
        )
        artifact_paths = [
            path
            for document in documents
            for path in (document.file_path, document.markdown_path)
            if path
        ] + image_paths + [
            source.file_path for source in analysis_files if source.file_path
        ]

        if message_ids:
            self.db.execute(
                delete(RetrievalLog).where(RetrievalLog.message_id.in_(message_ids))
            )
            self.db.execute(
                delete(LLMCallLog).where(LLMCallLog.message_id.in_(message_ids))
            )
            self.db.execute(
                delete(MaskingEvent).where(MaskingEvent.message_id.in_(message_ids))
            )
        if document_ids:
            self.db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "document",
                    AuditEvent.target_id.in_(document_ids),
                )
            )
            self.db.execute(
                delete(DocumentProcessingJob).where(
                    DocumentProcessingJob.document_id.in_(document_ids)
                )
            )
            self.db.execute(
                delete(DocumentPermission).where(
                    DocumentPermission.document_id.in_(document_ids)
                )
            )
            self.db.execute(
                delete(DocumentImage).where(
                    DocumentImage.document_id.in_(document_ids)
                )
            )
            self.db.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.document_id.in_(document_ids)
                )
            )
            self.db.execute(
                delete(Document).where(Document.id.in_(document_ids))
            )
        if analysis_job_ids:
            self.db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "analysis_job",
                    AuditEvent.target_id.in_(analysis_job_ids),
                )
            )
            self.db.execute(
                delete(AnalysisJob).where(AnalysisJob.id.in_(analysis_job_ids))
            )
        if analysis_file_ids:
            self.db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "analysis_file",
                    AuditEvent.target_id.in_(analysis_file_ids),
                )
            )
            self.db.execute(
                delete(AnalysisFile).where(AnalysisFile.id.in_(analysis_file_ids))
            )
        if message_ids:
            self.db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "chat_message",
                    AuditEvent.target_id.in_(message_ids),
                )
            )
            self.db.execute(
                delete(ChatMessage).where(ChatMessage.id.in_(message_ids))
            )
        self.db.execute(
            delete(AuditEvent).where(
                AuditEvent.target_type == "chat_session",
                AuditEvent.target_id == session_id,
            )
        )
        self.db.delete(chat_session)
        self.db.commit()

        deleted_files = sum(
            self._delete_artifact(path) for path in artifact_paths
        )
        return ChatSessionDeleteResponse(
            session_id=session_id,
            deleted_documents=len(document_ids),
            deleted_analysis_files=len(analysis_file_ids),
            deleted_messages=len(message_ids),
            deleted_files=deleted_files,
        )

    def _delete_artifact(self, raw_path: str) -> int:
        path = Path(raw_path).resolve()
        allowed_roots = [
            Path(settings.local_storage_root).resolve(),
            Path("data/extracted_images").resolve(),
        ]
        if not any(path.is_relative_to(root) for root in allowed_roots):
            return 0
        if not path.is_file():
            return 0
        try:
            path.unlink()
        except OSError:
            return 0
        for root in allowed_roots:
            if path.is_relative_to(root):
                parent = path.parent
                while parent != root and parent.is_dir():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
                break
        return 1
