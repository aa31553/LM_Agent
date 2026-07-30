import csv
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import polars as pl
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AnalysisJobStatus, ErrorCode, PermissionLevel
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.workspace import AnalysisArtifact
from app.schemas.analysis import (
    AnalysisChartArtifactRequest,
    AnalysisExportRequest,
    AnalysisReportRequest,
)
from app.services.analysis_job_service import AnalysisJobService
from app.services.audit_service import AuditService
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage


class AnalysisArtifactService:
    def __init__(self, db: Session, storage: WorkspaceStorage | None = None) -> None:
        self.db = db
        self.storage = storage or WorkspaceStorage()
        self.jobs = AnalysisJobService(db)

    def export(
        self,
        job_id: UUID,
        payload: AnalysisExportRequest,
        principal: Principal,
    ) -> AnalysisArtifact:
        job = self._completed_job(job_id, principal)
        result = job.result_json or {}
        rows = list(result.get("table", {}).get("rows", []))
        if len(rows) > settings.analysis_export_max_rows:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Analysis result exceeds the configured export row limit.",
                413,
            )
        default_name = f"analysis-{job.id}.{payload.format}"
        filename = self._filename(payload.filename or default_name, payload.format)
        if payload.format == "json":
            body = json.dumps(result, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            mime_type = "application/json"
        elif payload.format == "parquet":
            buffer = io.BytesIO()
            pl.DataFrame(rows).write_parquet(buffer, compression="zstd")
            body = buffer.getvalue()
            mime_type = "application/vnd.apache.parquet"
        else:
            text = io.StringIO()
            columns = [item["key"] for item in result.get("table", {}).get("columns", [])]
            if not columns and rows:
                columns = list(rows[0])
            writer = csv.DictWriter(text, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            body = text.getvalue().encode("utf-8-sig")
            mime_type = "text/csv; charset=utf-8"
        return self._create(
            job=job,
            artifact_type="export",
            filename=filename,
            mime_type=mime_type,
            body=body,
            metadata={"format": payload.format, "row_count": len(rows)},
            principal=principal,
        )

    def create_report(
        self,
        job_id: UUID,
        payload: AnalysisReportRequest,
        principal: Principal,
    ) -> AnalysisArtifact:
        job = self._completed_job(job_id, principal)
        result = job.result_json or {}
        summary = result.get("summary", {})
        lines = [
            f"# {payload.title}",
            "",
            f"- Analysis job: `{job.id}`",
            f"- Workspace: `{job.workspace_id}`",
            f"- Source file: `{job.file_id}`",
            f"- Processed rows: {summary.get('processed_rows', 0)}",
            f"- Matched rows: {summary.get('matched_rows', 0)}",
            f"- Result rows: {summary.get('result_rows', 0)}",
            "",
            "## Analysis plan",
            "",
            "```json",
            json.dumps(job.request_json, ensure_ascii=False, indent=2),
            "```",
        ]
        rows = result.get("table", {}).get("rows", [])
        if payload.include_result_rows:
            lines.extend(
                [
                    "",
                    "## Result data",
                    "",
                    "```json",
                    json.dumps(
                        rows[: payload.max_rows],
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    "```",
                ]
            )
        body = "\n".join(lines).encode("utf-8")
        return self._create(
            job=job,
            artifact_type="report",
            filename=f"analysis-report-{job.id}.md",
            mime_type="text/markdown; charset=utf-8",
            body=body,
            metadata={
                "title": payload.title,
                "included_rows": min(len(rows), payload.max_rows)
                if payload.include_result_rows
                else 0,
            },
            principal=principal,
        )

    def create_chart(
        self,
        job_id: UUID,
        payload: AnalysisChartArtifactRequest,
        principal: Principal,
    ) -> AnalysisArtifact:
        job = self._completed_job(job_id, principal)
        charts = (job.result_json or {}).get("charts", [])
        if payload.chart_index >= len(charts):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Chart index is outside the available chart list.",
                400,
                details={"chart_count": len(charts)},
            )
        chart = charts[payload.chart_index]
        filename = self._filename(
            payload.filename or f"chart-{job.id}-{payload.chart_index}.json",
            "json",
        )
        body = json.dumps(chart, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        return self._create(
            job=job,
            artifact_type="chart",
            filename=filename,
            mime_type="application/json",
            body=body,
            metadata={
                "chart_index": payload.chart_index,
                "schema_version": chart.get("schema_version"),
            },
            principal=principal,
        )

    def create_hybrid_report(
        self,
        *,
        workspace_id: UUID,
        job_ids: list[UUID],
        question: str,
        answer: str,
        citations: list[dict],
        principal: Principal,
    ) -> AnalysisArtifact:
        WorkspaceService(self.db).get(
            workspace_id,
            principal,
            PermissionLevel.WRITE,
        )
        jobs = [
            self.jobs.get(job_id, principal, required=PermissionLevel.READ) for job_id in job_ids
        ]
        if any(job.workspace_id != workspace_id for job in jobs):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Hybrid report jobs must belong to one workspace.",
                400,
            )
        lines = [
            "# Hybrid analysis report",
            "",
            f"Question: {question}",
            "",
            "## Grounded answer",
            "",
            answer,
            "",
            "## Analysis jobs",
            "",
            *[f"- `{job_id}`" for job_id in job_ids],
            "",
            "## Knowledge citations",
            "",
            "```json",
            json.dumps(citations, ensure_ascii=False, indent=2, default=str),
            "```",
        ]
        return self._create_raw(
            workspace_id=workspace_id,
            file_id=jobs[0].file_id if jobs else None,
            job_id=jobs[0].id if jobs else None,
            artifact_type="hybrid_report",
            filename=f"hybrid-report-{uuid4()}.md",
            mime_type="text/markdown; charset=utf-8",
            body="\n".join(lines).encode("utf-8"),
            metadata={"analysis_job_ids": [str(item) for item in job_ids]},
            principal=principal,
        )

    def get(
        self,
        artifact_id: UUID,
        principal: Principal,
    ) -> AnalysisArtifact:
        artifact = self.db.get(AnalysisArtifact, artifact_id)
        if artifact is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis artifact not found.",
                404,
            )
        WorkspaceService(self.db).get(
            artifact.workspace_id,
            principal,
            PermissionLevel.READ,
        )
        return artifact

    def _completed_job(self, job_id: UUID, principal: Principal):
        job = self.jobs.get(job_id, principal, required=PermissionLevel.WRITE)
        if job.status != AnalysisJobStatus.COMPLETED.value or job.result_json is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Only completed analysis jobs can create artifacts.",
                409,
            )
        return job

    def _create(
        self,
        *,
        job,
        artifact_type: str,
        filename: str,
        mime_type: str,
        body: bytes,
        metadata: dict,
        principal: Principal,
    ) -> AnalysisArtifact:
        return self._create_raw(
            workspace_id=job.workspace_id,
            file_id=job.file_id,
            job_id=job.id,
            artifact_type=artifact_type,
            filename=filename,
            mime_type=mime_type,
            body=body,
            metadata=metadata,
            principal=principal,
        )

    def _create_raw(
        self,
        *,
        workspace_id,
        file_id,
        job_id,
        artifact_type: str,
        filename: str,
        mime_type: str,
        body: bytes,
        metadata: dict,
        principal: Principal,
    ) -> AnalysisArtifact:
        user = AuditService(self.db).ensure_user(principal)
        artifact = AnalysisArtifact(
            id=uuid4(),
            workspace_id=workspace_id,
            file_id=file_id,
            job_id=job_id,
            artifact_type=artifact_type,
            filename=Path(filename).name,
            file_path="pending",
            mime_type=mime_type,
            size_bytes=len(body),
            artifact_metadata=metadata,
            created_by=user.id,
        )
        artifact.file_path = self.storage.write_artifact_bytes(
            workspace_id,
            artifact.id,
            artifact.filename,
            body,
        )
        self.db.add(artifact)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.storage.delete_artifact_tree(workspace_id, artifact.id)
            raise
        self.db.refresh(artifact)
        return artifact

    @staticmethod
    def _filename(filename: str, extension: str) -> str:
        safe = Path(filename).name
        if not safe.lower().endswith(f".{extension}"):
            safe = f"{safe}.{extension}"
        return safe
