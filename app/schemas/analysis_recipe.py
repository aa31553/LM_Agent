from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.analysis import AnalysisNormalizationAction, AnalysisPlan, DatasetSource


class RecipeParameter(BaseModel):
    type: Literal[
        "column",
        "columns",
        "strings",
        "number",
        "integer",
        "boolean",
        "string",
        "enum",
    ]
    required: bool = False
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str] = Field(default_factory=list)
    description: str = ""


class RecipeDefinition(BaseModel):
    recipe_id: str
    version: str
    category: str
    description: str
    parameters: dict[str, RecipeParameter]
    result_schema: list[str]
    chart_semantic_type: str | None = None
    enabled: bool = True


class RecipeListResponse(BaseModel):
    items: list[RecipeDefinition]


class IntentDraft(BaseModel):
    schema_version: str = "1.0"
    sources: list[DatasetSource] = Field(min_length=1, max_length=8)
    recipe_id: str = Field(min_length=1, max_length=64)
    recipe_version: str = Field(default="1.0", min_length=1, max_length=16)
    inputs: dict[str, Any] = Field(default_factory=dict)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    chart_enabled: bool = True
    title: str | None = Field(default=None, max_length=255)


class AnalysisIntentValidateRequest(BaseModel):
    workspace_id: UUID
    intent: IntentDraft


class AnalysisIntentValidationResponse(BaseModel):
    valid: bool = True
    normalized_intent: IntentDraft
    executable_plan: AnalysisPlan
    normalization_actions: list[AnalysisNormalizationAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RecipeMetricItem(BaseModel):
    recipe_id: str
    job_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    success_rate: float | None = None
    draft_count: int
    validation_failure_rate: float | None = None
    repair_rate: float | None = None
    average_duration_ms: float | None = None
    p95_duration_ms: float | None = None


class RecipeMetricsResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    items: list[RecipeMetricItem]
