from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import ceil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import AnalysisJobStatus, PermissionLevel
from app.core.security import Principal
from app.models.analysis import AnalysisJob, AnalysisPlanDraft
from app.schemas.analysis_recipe import RecipeMetricItem, RecipeMetricsResponse
from app.services.workspace_service import WorkspaceService


class AnalysisMetricsService:
    """Workspace-scoped operational metrics for deterministic recipes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def summarize(
        self,
        workspace_id: UUID,
        principal: Principal,
    ) -> RecipeMetricsResponse:
        workspace = WorkspaceService(self.db).get(
            workspace_id,
            principal,
            PermissionLevel.READ,
        )
        jobs = list(
            self.db.scalars(
                select(AnalysisJob).where(
                    AnalysisJob.workspace_id == workspace.id,
                    AnalysisJob.recipe_id.is_not(None),
                )
            )
        )
        drafts = list(
            self.db.scalars(
                select(AnalysisPlanDraft).where(
                    AnalysisPlanDraft.workspace_id == workspace.id,
                    AnalysisPlanDraft.recipe_id.is_not(None),
                )
            )
        )
        jobs_by_recipe: dict[str, list[AnalysisJob]] = defaultdict(list)
        drafts_by_recipe: dict[str, list[AnalysisPlanDraft]] = defaultdict(list)
        for job in jobs:
            jobs_by_recipe[str(job.recipe_id)].append(job)
        for draft in drafts:
            drafts_by_recipe[str(draft.recipe_id)].append(draft)
        items = []
        for recipe_id in sorted(set(jobs_by_recipe) | set(drafts_by_recipe)):
            recipe_jobs = jobs_by_recipe[recipe_id]
            recipe_drafts = drafts_by_recipe[recipe_id]
            completed = sum(
                job.status == AnalysisJobStatus.COMPLETED.value for job in recipe_jobs
            )
            failed = sum(
                job.status == AnalysisJobStatus.FAILED.value for job in recipe_jobs
            )
            cancelled = sum(
                job.status == AnalysisJobStatus.CANCELLED.value for job in recipe_jobs
            )
            terminal = completed + failed
            validation_failures = sum(
                bool(draft.validation_errors_json) for draft in recipe_drafts
            )
            repairs = sum(bool(draft.repair_attempted) for draft in recipe_drafts)
            durations = sorted(
                int(job.execution_duration_ms)
                for job in recipe_jobs
                if job.execution_duration_ms is not None
            )
            items.append(
                RecipeMetricItem(
                    recipe_id=recipe_id,
                    job_count=len(recipe_jobs),
                    completed_count=completed,
                    failed_count=failed,
                    cancelled_count=cancelled,
                    success_rate=completed / terminal if terminal else None,
                    draft_count=len(recipe_drafts),
                    validation_failure_rate=(
                        validation_failures / len(recipe_drafts)
                        if recipe_drafts
                        else None
                    ),
                    repair_rate=repairs / len(recipe_drafts) if recipe_drafts else None,
                    average_duration_ms=(
                        sum(durations) / len(durations) if durations else None
                    ),
                    p95_duration_ms=self._percentile_95(durations),
                )
            )
        return RecipeMetricsResponse(
            workspace_id=workspace.id,
            generated_at=datetime.utcnow(),
            items=items,
        )

    @staticmethod
    def _percentile_95(values: list[int]) -> float | None:
        if not values:
            return None
        return float(values[max(0, ceil(0.95 * len(values)) - 1)])
