from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.constants import AnalysisJobStatus
from app.schemas.chat import LLMUsage

Scalar = str | int | float | bool | date | datetime | None


class SpreadsheetColumnInfo(BaseModel):
    name: str
    inferred_type: str
    sample_values: list[Scalar] = Field(default_factory=list)
    null_count_in_sample: int = 0


class SpreadsheetSheetInfo(BaseModel):
    name: str
    row_count: int | None = None
    column_count: int
    columns: list[SpreadsheetColumnInfo]
    sample_rows: list[dict[str, Scalar]] = Field(default_factory=list)


class SpreadsheetInspectionResponse(BaseModel):
    file_id: UUID
    filename: str
    file_type: str
    sheets: list[SpreadsheetSheetInfo]


class AnalysisFileUploadResponse(BaseModel):
    file_id: UUID
    session_id: UUID
    filename: str
    file_type: str
    size_bytes: int
    status: str = "ready"
    expires_at: datetime | None = None


class AnalysisFileItem(AnalysisFileUploadResponse):
    created_at: datetime


class AnalysisFileListResponse(BaseModel):
    session_id: UUID
    items: list[AnalysisFileItem]


class FilterCondition(BaseModel):
    column: str
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "contains",
        "in",
        "is_null",
        "not_null",
    ]
    value: Scalar | list[Scalar] = None


class AggregationSpec(BaseModel):
    function: Literal["count", "sum", "mean", "std", "min", "max", "count_if"]
    column: str | None = None
    alias: str = Field(min_length=1, max_length=128)
    condition: FilterCondition | None = None

    @model_validator(mode="after")
    def validate_function_arguments(self):
        if self.function not in {"count", "count_if"} and self.column is None:
            raise ValueError(f"column is required for {self.function}")
        if self.function == "count_if" and self.condition is None:
            raise ValueError("condition is required for count_if")
        return self


class SortSpec(BaseModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "scatter"]
    x_field: str
    y_field: str
    title: str = Field(default="Analysis result", max_length=255)


class AnalysisPlan(BaseModel):
    sheet: str | None = None
    select: list[str] = Field(default_factory=list, max_length=50)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    filters: list[FilterCondition] = Field(default_factory=list, max_length=20)
    aggregations: list[AggregationSpec] = Field(default_factory=list, max_length=20)
    sort: list[SortSpec] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=1000, ge=1, le=5000)
    charts: list[ChartSpec] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def require_output(self):
        if not self.select and not self.aggregations:
            raise ValueError("select or aggregations is required")
        return self


class AnalysisPlanValidateRequest(BaseModel):
    file_id: UUID
    plan: AnalysisPlan


class AnalysisPlanValidationResponse(BaseModel):
    valid: bool = True
    normalized_plan: AnalysisPlan
    warnings: list[str] = Field(default_factory=list)


class AnalysisJobCreate(BaseModel):
    file_id: UUID
    plan: AnalysisPlan


class AnalysisJobResponse(BaseModel):
    job_id: UUID
    session_id: UUID
    file_id: UUID
    status: AnalysisJobStatus
    plan: AnalysisPlan
    result: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class AnalysisExplanationResponse(BaseModel):
    job_id: UUID
    answer: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
