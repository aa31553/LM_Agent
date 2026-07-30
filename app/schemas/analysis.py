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
    formula_cells_in_sample: int = 0
    formatted_cells_in_sample: int = 0
    warnings: list[str] = Field(default_factory=list)


class SpreadsheetInspectionResponse(BaseModel):
    file_id: UUID
    workspace_id: UUID
    filename: str
    file_type: str
    sheets: list[SpreadsheetSheetInfo]
    warnings: list[str] = Field(default_factory=list)


class AnalysisFileUploadResponse(BaseModel):
    file_id: UUID
    workspace_id: UUID
    session_id: UUID | None = None
    filename: str
    file_type: str
    size_bytes: int
    status: str = "ready"
    expires_at: datetime | None = None


class AnalysisFileItem(AnalysisFileUploadResponse):
    created_at: datetime


class AnalysisFileListResponse(BaseModel):
    workspace_id: UUID
    session_id: UUID | None = None
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

    @model_validator(mode="after")
    def validate_value(self):
        if self.operator not in {"is_null", "not_null"} and self.value is None:
            raise ValueError(f"value is required for {self.operator}")
        return self


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
    def validate_output(self):
        if not self.select and not self.aggregations:
            raise ValueError("select or aggregations is required")
        aliases = [item.alias for item in self.aggregations]
        duplicate_aliases = sorted(alias for alias in set(aliases) if aliases.count(alias) > 1)
        if duplicate_aliases:
            raise ValueError("aggregation aliases must be unique: " + ", ".join(duplicate_aliases))
        conflicting_aliases = sorted(set(aliases) & set(self.group_by))
        if conflicting_aliases:
            raise ValueError(
                "aggregation aliases must not match group_by columns: "
                + ", ".join(conflicting_aliases)
            )
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
    session_id: UUID | None = None
    plan: AnalysisPlan


class AnalysisJobResponse(BaseModel):
    job_id: UUID
    workspace_id: UUID
    session_id: UUID | None = None
    file_id: UUID
    status: AnalysisJobStatus
    plan: AnalysisPlan
    progress: int = Field(default=0, ge=0, le=100)
    result: dict[str, Any] | None = None
    error_message: str | None = None
    retry_of_job_id: UUID | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class AnalysisJobListResponse(BaseModel):
    workspace_id: UUID
    session_id: UUID | None = None
    items: list[AnalysisJobResponse]
    total: int
    page: int
    page_size: int


class AnalysisExplanationResponse(BaseModel):
    job_id: UUID
    answer: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
