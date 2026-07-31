import math
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnalysisFileStatus,
    AnalysisJobStatus,
    ConfidentialLevel,
    ErrorCode,
)
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.base import Base
from app.models.analysis import AnalysisFile, AnalysisJob, AnalysisPlanDraft
from app.schemas.analysis import DatasetSource
from app.schemas.analysis_recipe import IntentDraft
from app.services.analysis_metrics_service import AnalysisMetricsService
from app.services.analysis_job_service import _structured_analysis_error
from app.services.analysis_orchestrator_service import AnalysisOrchestratorService
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler
from app.services.analysis_recipe_executor import AnalysisRecipeExecutor
from app.services.analysis_recipe_registry import AnalysisRecipeRegistry
from app.services.audit_service import AuditService
from app.services.spreadsheet_ingestion_service import SpreadsheetIngestionService
from app.services.workspace_service import WorkspaceService
from app.workers.process_runner import (
    ProcessWorkerCancelledError,
    ProcessWorkerTimeoutError,
    run_in_process,
)


def _manifest(tmp_path: Path, rows: list[dict]):
    file_id = uuid4()
    path = tmp_path / f"{file_id}.parquet"
    pl.DataFrame(rows).write_parquet(path)
    dataframe = pl.read_parquet(path)
    columns = [
        {
            "name": name,
            "column_id": f"csv:c{index:04d}",
            "display_name": name,
            "normalized_name": name.lower(),
            "inferred_type": SpreadsheetIngestionService._polars_type(dtype),
        }
        for index, (name, dtype) in enumerate(dataframe.schema.items(), start=1)
    ]
    manifest = {
        "datasets": [
            {
                "key": "csv",
                "sheet": "CSV",
                "path": str(path),
                "row_count": dataframe.height,
                "columns": columns,
            }
        ]
    }
    return file_id, DatasetSource(file_id=file_id, alias="data", sheet="CSV"), manifest


def _execute(tmp_path: Path, recipe_id: str, inputs: dict, rows: list[dict]):
    file_id, source, manifest = _manifest(tmp_path, rows)
    intent = IntentDraft(sources=[source], recipe_id=recipe_id, inputs=inputs)
    _normalized, plan, _actions, _warnings = AnalysisRecipeCompiler().compile(
        intent,
        {str(file_id): manifest},
        allowed_file_ids={str(file_id)},
    )
    return AnalysisRecipeExecutor().execute(plan, {str(file_id): manifest})


def _passthrough(value):
    return value


def test_phase5_registry_contains_versioned_quality_recipes() -> None:
    recipe_ids = {item.recipe_id for item in AnalysisRecipeRegistry().list_enabled()}
    assert {
        "descriptive_statistics",
        "boxplot_summary",
        "outlier_iqr",
        "correlation_matrix",
        "yield_summary",
        "spec_judgement",
        "process_capability",
        "control_chart",
    }.issubset(recipe_ids)


def test_clean_number_accepts_integer_control_limits() -> None:
    assert AnalysisRecipeExecutor._clean_number(1) == 1


def test_descriptive_boxplot_and_iqr_definitions(tmp_path: Path) -> None:
    rows = [{"value": value} for value in [1, 2, 3, 4, 100]] + [{"value": None}]
    descriptive = _execute(
        tmp_path,
        "descriptive_statistics",
        {"source_field": "value", "std_mode": "sample"},
        rows,
    )
    stats = descriptive["table"]["rows"][0]
    assert stats["count"] == 5
    assert stats["null_count"] == 1
    assert stats["mean"] == 22
    assert stats["median"] == 3
    assert stats["std_definition"] == "sample"
    assert math.isclose(stats["std"], 43.61765697512877)

    boxplot = _execute(
        tmp_path,
        "boxplot_summary",
        {"source_field": "value"},
        rows,
    )
    box = boxplot["table"]["rows"][0]
    assert box == {
        "group": "all",
        "min": 1,
        "q1": 2,
        "median": 3,
        "q3": 4,
        "max": 4,
        "whisker_low": -1,
        "whisker_high": 7,
        "outlier_count": 1,
        "outliers": [100],
    }
    assert boxplot["charts"][0]["semantic_type"] == "boxplot"

    outliers = _execute(
        tmp_path,
        "outlier_iqr",
        {"source_field": "value"},
        rows,
    )
    assert outliers["table"]["rows"] == [
        {
            "lower_bound": -1,
            "upper_bound": 7,
            "outlier_count": 1,
            "outlier_ratio": 0.2,
        }
    ]


def test_correlation_yield_and_spec_judgement(tmp_path: Path) -> None:
    correlation = _execute(
        tmp_path,
        "correlation_matrix",
        {"fields": ["x", "y"], "method": "pearson"},
        [
            {"x": 1, "y": 2},
            {"x": 2, "y": 4},
            {"x": 3, "y": 6},
            {"x": None, "y": 8},
        ],
    )
    cross = next(
        row
        for row in correlation["table"]["rows"]
        if row["x_category"] == "data.x" and row["y_category"] == "data.y"
    )
    assert cross["value"] == 1
    assert cross["sample_size"] == 3
    assert correlation["charts"][0]["semantic_type"] == "heatmap"
    assert "does not imply causation" in correlation["summary"]["warnings"][0]

    yield_result = _execute(
        tmp_path,
        "yield_summary",
        {"status_field": "status", "ok_values": ["OK", "PASS"]},
        [{"status": "OK"}, {"status": "NG"}, {"status": "PASS"}, {"status": None}],
    )
    assert yield_result["summary"]["yield_rate"] == 2 / 3
    assert yield_result["summary"]["null_count"] == 1
    assert yield_result["table"]["rows"] == [
        {"status": "OK", "count": 2, "ratio": 2 / 3},
        {"status": "NG", "count": 1, "ratio": 1 / 3},
    ]

    spec = _execute(
        tmp_path,
        "spec_judgement",
        {"source_field": "value", "lsl": 10, "usl": 15, "inclusive": True},
        [{"value": 5}, {"value": 10}, {"value": 15}, {"value": 20}, {"value": None}],
    )
    assert spec["summary"]["within_spec_count"] == 2
    assert spec["summary"]["below_lsl_count"] == 1
    assert spec["summary"]["above_usl_count"] == 1
    assert spec["summary"]["null_count"] == 1


def test_process_capability_definitions_and_warnings(tmp_path: Path) -> None:
    result = _execute(
        tmp_path,
        "process_capability",
        {"source_field": "value", "lsl": 5, "usl": 15},
        [{"value": value} for value in [8, 9, 10, 11, 12]],
    )
    row = result["table"]["rows"][0]
    assert row["sample_size"] == 5
    assert row["mean"] == 10
    assert math.isclose(row["overall_std"], math.sqrt(2.5))
    assert math.isclose(row["within_std"], 1 / 1.128)
    assert math.isclose(row["cp"], 1.88, rel_tol=1e-3)
    assert math.isclose(row["cpk"], 1.88, rel_tol=1e-3)
    assert math.isclose(row["pp"], 1.0540925533894598)
    assert math.isclose(row["ppk"], 1.0540925533894598)
    assert row["lsl"] == 5
    assert row["usl"] == 15
    assert result["summary"]["calculation_parameters"]["within_sigma"] == "MRbar/d2"
    assert any("30" in warning for warning in result["summary"]["warnings"])


@pytest.mark.parametrize("chart_type", ["i_mr", "xbar_r", "p"])
def test_control_chart_contracts(tmp_path: Path, chart_type: str) -> None:
    if chart_type == "i_mr":
        inputs = {"chart_type": chart_type, "source_field": "value"}
        rows = [{"value": value} for value in [10, 11, 9, 10]]
    elif chart_type == "xbar_r":
        inputs = {
            "chart_type": chart_type,
            "source_field": "value",
            "subgroup_field": "batch",
        }
        rows = [
            {"batch": "A", "value": 1},
            {"batch": "A", "value": 2},
            {"batch": "A", "value": 3},
            {"batch": "B", "value": 2},
            {"batch": "B", "value": 3},
            {"batch": "B", "value": 4},
        ]
    else:
        inputs = {
            "chart_type": chart_type,
            "status_field": "status",
            "subgroup_field": "batch",
            "ok_values": ["OK"],
        }
        rows = [
            {"batch": "A", "status": "OK"},
            {"batch": "A", "status": "NG"},
            {"batch": "B", "status": "OK"},
            {"batch": "B", "status": "OK"},
        ]
    result = _execute(tmp_path, "control_chart", inputs, rows)
    assert result["table"]["rows"]
    assert {
        "period",
        "value",
        "center_line",
        "ucl",
        "lcl",
    }.issubset(result["table"]["rows"][0])
    assert result["charts"][0]["semantic_type"] == "control_chart"
    assert result["charts"][0]["x_field"] == "period"
    assert result["charts"][0]["y_field"] == "value"
    assert result["summary"]["control_chart_type"] == chart_type


def test_phase6_feature_flags_switch_prompt_and_recipe_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "analysis_intent_flow_enabled", False)
    system_prompt, user_prompt = AnalysisOrchestratorService._planning_prompts(
        question="平均值",
        schema_context="{}",
    )
    assert "AnalysisPlan" in system_prompt + user_prompt
    assert "IntentDraft" not in system_prompt + user_prompt

    monkeypatch.setattr(settings, "analysis_intent_flow_enabled", True)
    system_prompt, user_prompt = AnalysisOrchestratorService._planning_prompts(
        question="平均值",
        schema_context="{}",
    )
    assert "IntentDraft" in system_prompt + user_prompt
    assert "period_overlay" in system_prompt + user_prompt

    monkeypatch.setattr(settings, "analysis_recipes_enabled", False)
    file_id, source, manifest = _manifest(tmp_path, [{"value": 1}])
    intent = IntentDraft(
        sources=[source],
        recipe_id="descriptive_statistics",
        inputs={"source_field": "value"},
    )
    with pytest.raises(APIError, match="disabled") as exc_info:
        AnalysisRecipeCompiler().compile(
            intent,
            {str(file_id): manifest},
            allowed_file_ids={str(file_id)},
        )
    assert exc_info.value.error_code == ErrorCode.RECIPE_NOT_FOUND


def test_phase6_metrics_report_success_validation_repair_and_duration(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    principal = Principal(
        external_user_id="metrics-user",
        username="metrics-user",
        clearance_level=ConfidentialLevel.INTERNAL,
    )
    with Session(engine) as db:
        user = AuditService(db).ensure_user(principal)
        workspace = WorkspaceService(db).get_or_create_personal(principal)
        source = AnalysisFile(
            workspace_id=workspace.id,
            created_by=user.id,
            filename="metrics.csv",
            original_filename="metrics.csv",
            file_type="csv",
            file_path=str(tmp_path / "metrics.csv"),
            size_bytes=1,
            confidential_level=ConfidentialLevel.INTERNAL.value,
            status=AnalysisFileStatus.READY.value,
        )
        db.add(source)
        db.flush()
        for status, duration in [
            (AnalysisJobStatus.COMPLETED.value, 120),
            (AnalysisJobStatus.FAILED.value, 80),
            (AnalysisJobStatus.CANCELLED.value, 20),
        ]:
            db.add(
                AnalysisJob(
                    workspace_id=workspace.id,
                    file_id=source.id,
                    created_by=user.id,
                    status=status,
                    request_json={},
                    recipe_id="histogram",
                    recipe_version="1.0",
                    execution_duration_ms=duration,
                )
            )
        db.add_all(
            [
                AnalysisPlanDraft(
                    workspace_id=workspace.id,
                    created_by=user.id,
                    question="histogram",
                    source_file_ids=[str(source.id)],
                    plan_json={},
                    recipe_id="histogram",
                    validation_errors_json=["initial error"],
                    repair_attempted=True,
                ),
                AnalysisPlanDraft(
                    workspace_id=workspace.id,
                    created_by=user.id,
                    question="histogram",
                    source_file_ids=[str(source.id)],
                    plan_json={},
                    recipe_id="histogram",
                ),
            ]
        )
        db.commit()
        metrics = AnalysisMetricsService(db).summarize(workspace.id, principal)
    histogram = metrics.items[0]
    assert histogram.recipe_id == "histogram"
    assert histogram.job_count == 3
    assert histogram.success_rate == 0.5
    assert histogram.validation_failure_rate == 0.5
    assert histogram.repair_rate == 0.5
    assert math.isclose(histogram.average_duration_ms, 220 / 3)


def test_phase6_structured_errors_and_additive_migration() -> None:
    code, details = _structured_analysis_error(
        APIError(
            ErrorCode.RECIPE_PARAMETER_INVALID,
            "invalid",
            422,
            details={"parameter": "lsl"},
        )
    )
    assert code == ErrorCode.RECIPE_PARAMETER_INVALID.value
    assert details == {"parameter": "lsl"}
    timeout_code, timeout_details = _structured_analysis_error(
        ProcessWorkerTimeoutError("timeout")
    )
    assert timeout_code == "ANALYSIS_TIMEOUT"
    assert timeout_details["exception_type"] == "ProcessWorkerTimeoutError"

    columns = set(AnalysisJob.__table__.columns.keys())
    assert {"error_code", "error_details_json", "execution_duration_ms"} <= columns
    migration = Path(
        "app/db/migrations/999_session_analysis_workspace_upgrade_phase5_6.sql"
    ).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS error_code" in migration
    assert "ADD COLUMN IF NOT EXISTS execution_duration_ms" in migration


def test_phase6_load_security_and_cancel_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "analysis_recipes_enabled", True)
    rows = [{"x": value, "y": value * 2} for value in range(20_000)]
    result = _execute(
        tmp_path,
        "correlation_matrix",
        {"fields": ["x", "y"], "method": "spearman"},
        rows,
    )
    assert len(result["table"]["rows"]) == 4
    assert result["summary"]["matched_rows"] == 20_000

    registry = AnalysisRecipeRegistry()
    with pytest.raises(APIError, match="undeclared") as exc_info:
        registry.normalize_inputs(
            registry.get("descriptive_statistics"),
            {
                "source_field": "x",
                "python": "__import__('os').system('whoami')",
                "path": "../../etc/passwd",
            },
        )
    assert exc_info.value.error_code == ErrorCode.RECIPE_PARAMETER_INVALID

    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return True

    with pytest.raises(ProcessWorkerCancelledError):
        run_in_process(
            _passthrough,
            timeout_seconds=5,
            kwargs={"value": "never returned"},
            cancel_check=cancelled,
            poll_interval_seconds=0.01,
        )
    assert calls >= 1
