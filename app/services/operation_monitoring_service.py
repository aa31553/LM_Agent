from time import perf_counter

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent, LLMCallLog, RetrievalLog
from app.models.document import Document, DocumentProcessingJob
from app.models.masking import MaskingEvent
from app.schemas.admin import OperationMetricsResponse


class OperationMonitoringService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def metrics(self) -> OperationMetricsResponse:
        started = perf_counter()
        self.db.execute(text("select 1"))
        latency_ms = int((perf_counter() - started) * 1000)
        return OperationMetricsResponse(
            database={"status": "ok", "latency_ms": latency_ms},
            documents=self._counts_by(Document.status),
            processing_jobs=self._counts_by(DocumentProcessingJob.status),
            audit={
                "retrieval_logs": self._count(RetrievalLog),
                "masking_events": self._count(MaskingEvent),
                "llm_call_logs": self._count(LLMCallLog),
                "audit_events": self._count(AuditEvent),
            },
        )

    def _counts_by(self, column) -> dict[str, int]:
        rows = self.db.query(column, func.count()).group_by(column).all()
        return {str(key): int(count) for key, count in rows}

    def _count(self, model) -> int:
        return int(self.db.query(func.count(model.id)).scalar() or 0)
