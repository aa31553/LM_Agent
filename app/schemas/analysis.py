from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.constants import AnalysisJobStatus, AnalysisPlanDraftStatus
from app.schemas.chat import Citation, LLMUsage

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
    status: str = "profile_queued"
    profile_progress: int = Field(default=0, ge=0, le=100)
    profile_error: str | None = None
    dataset_count: int = 0
    profiled_at: datetime | None = None
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
    function: Literal[
        "count",
        "distinct_count",
        "sum",
        "mean",
        "std",
        "min",
        "max",
        "count_if",
        "percentile",
    ]
    column: str | None = None
    alias: str = Field(min_length=1, max_length=128)
    condition: FilterCondition | None = None
    percentile: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_function_arguments(self):
        if self.function not in {"count", "count_if"} and self.column is None:
            raise ValueError(f"column is required for {self.function}")
        if self.function == "count_if" and self.condition is None:
            raise ValueError("condition is required for count_if")
        if self.function == "percentile" and self.percentile is None:
            raise ValueError("percentile is required for percentile aggregation")
        return self


class SortSpec(BaseModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "scatter"]
    x_field: str
    y_field: str
    title: str = Field(default="Analysis result", max_length=255)
    series_field: str | None = None
    x_type: Literal["category", "value", "time"] = "category"
    y_unit: str | None = Field(default=None, max_length=32)
    decimal_places: int = Field(default=2, ge=0, le=10)
    tooltip_fields: list[str] = Field(default_factory=list, max_length=20)
    zoom: bool = False


class DatasetSource(BaseModel):
    file_id: UUID
    alias: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    sheet: str | None = None


class JoinSpec(BaseModel):
    left_alias: str
    right_alias: str
    left_on: list[str] = Field(min_length=1, max_length=10)
    right_on: list[str] = Field(min_length=1, max_length=10)
    how: Literal["inner", "left", "right", "full"] = "inner"

    @model_validator(mode="after")
    def validate_keys(self):
        if len(self.left_on) != len(self.right_on):
            raise ValueError("left_on and right_on must contain the same number of keys")
        if self.left_alias == self.right_alias:
            raise ValueError("join aliases must be different")
        return self


class DateBucketSpec(BaseModel):
    column: str
    unit: Literal["day", "week", "month", "quarter", "year"]
    alias: str = Field(min_length=1, max_length=128)


class PivotSpec(BaseModel):
    index: list[str] = Field(min_length=1, max_length=5)
    columns: str
    values: str
    aggregation: Literal["count", "sum", "mean", "min", "max"] = "sum"


class CorrelationSpec(BaseModel):
    columns: list[str] = Field(min_length=2, max_length=20)
    method: Literal["pearson", "spearman"] = "pearson"


class AnalysisPlan(BaseModel):
    sheet: str | None = None
    sources: list[DatasetSource] = Field(default_factory=list, max_length=8)
    joins: list[JoinSpec] = Field(default_factory=list, max_length=7)
    select: list[str] = Field(default_factory=list, max_length=50)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    filters: list[FilterCondition] = Field(default_factory=list, max_length=20)
    aggregations: list[AggregationSpec] = Field(default_factory=list, max_length=20)
    date_buckets: list[DateBucketSpec] = Field(default_factory=list, max_length=5)
    pivot: PivotSpec | None = None
    correlation: CorrelationSpec | None = None
    sort: list[SortSpec] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=1000, ge=1, le=5000)
    charts: list[ChartSpec] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_output(self):
        if (
            not self.select
            and not self.aggregations
            and self.pivot is None
            and self.correlation is None
        ):
            raise ValueError("select, aggregations, pivot, or correlation is required")
        source_aliases = [source.alias for source in self.sources]
        if len(source_aliases) != len(set(source_aliases)):
            raise ValueError("dataset source aliases must be unique")
        if self.joins and len(self.sources) < 2:
            raise ValueError("joins require at least two dataset sources")
        unknown_aliases = {
            alias
            for join in self.joins
            for alias in (join.left_alias, join.right_alias)
            if alias not in source_aliases
        }
        if unknown_aliases:
            raise ValueError(
                "join references unknown source aliases: " + ", ".join(sorted(unknown_aliases))
            )
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
        derived_aliases = [item.alias for item in self.date_buckets]
        if len(derived_aliases) != len(set(derived_aliases)):
            raise ValueError("date bucket aliases must be unique")
        if set(derived_aliases) & set(aliases):
            raise ValueError("date bucket aliases must not match aggregation aliases")
        if self.pivot is not None and self.correlation is not None:
            raise ValueError("pivot and correlation cannot be requested together")
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
    draft_id: UUID | None = None
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


class AnalysisPlanDraftCreate(BaseModel):
    workspace_id: UUID
    question: str = Field(min_length=2, max_length=4000)
    file_ids: list[UUID] = Field(min_length=1, max_length=8)
    session_id: UUID | None = None


class AnalysisNormalizationAction(BaseModel):
    code: str
    path: str | None = None
    source_field: str | None = None
    target_field: str | None = None


class AnalysisPlanDraftResponse(BaseModel):
    draft_id: UUID
    workspace_id: UUID
    session_id: UUID | None = None
    question: str
    file_ids: list[UUID]
    plan: AnalysisPlan
    warnings: list[str] = Field(default_factory=list)
    normalization_actions: list[AnalysisNormalizationAction] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    repair_attempted: bool = False
    status: AnalysisPlanDraftStatus
    confirmation_required: bool = True
    usage: LLMUsage = Field(default_factory=LLMUsage)
    created_at: datetime
    confirmed_at: datetime | None = None


class AnalysisPlanDraftConfirmRequest(BaseModel):
    plan: AnalysisPlan | None = None


class AnalysisHybridRequest(BaseModel):
    workspace_id: UUID
    question: str = Field(min_length=2, max_length=4000)
    analysis_job_ids: list[UUID] = Field(min_length=1, max_length=10)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=8, ge=1, le=30)
    use_rerank: bool = True
    create_report: bool = True


class AnalysisHybridResponse(BaseModel):
    workspace_id: UUID
    answer: str
    analysis_job_ids: list[UUID]
    citations: list[Citation] = Field(default_factory=list)
    report_artifact_id: UUID | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)


class AnalysisExportRequest(BaseModel):
    format: Literal["csv", "json", "parquet"] = "csv"
    filename: str | None = Field(default=None, max_length=255)


class AnalysisReportRequest(BaseModel):
    title: str = Field(default="Analysis report", min_length=1, max_length=255)
    include_result_rows: bool = True
    max_rows: int = Field(default=200, ge=1, le=5000)


class AnalysisChartArtifactRequest(BaseModel):
    chart_index: int = Field(default=0, ge=0)
    filename: str | None = Field(default=None, max_length=255)


class AnalysisArtifactCreatedResponse(BaseModel):
    artifact_id: UUID
    workspace_id: UUID
    job_id: UUID
    artifact_type: str
    filename: str
    mime_type: str
    size_bytes: int
