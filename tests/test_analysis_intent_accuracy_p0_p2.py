import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AnalysisFileStatus, ConfidentialLevel, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.base import Base
from app.models.analysis import AnalysisFile
from app.schemas.analysis import AnalysisPlanDraftCreate, DatasetSource, FilterCondition
from app.schemas.analysis_recipe import IntentDraft
from app.services.analysis_intent_quality_service import AnalysisIntentQualityService
from app.services.analysis_intent_semantic_service import AnalysisIntentSemanticService
from app.services.analysis_orchestrator_service import AnalysisOrchestratorService
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler
from app.services.analysis_recipe_executor import AnalysisRecipeExecutor
from app.services.audit_service import AuditService
from app.services.workspace_service import WorkspaceService


def _manifest(tmp_path: Path, rows: list[dict]):
    file_id = uuid4()
    path = tmp_path / f"{file_id}.parquet"
    frame = pl.DataFrame(rows)
    frame.write_parquet(path)
    columns = []
    for index, (name, dtype) in enumerate(frame.schema.items(), start=1):
        inferred_type = (
            "integer"
            if dtype.is_integer()
            else "number"
            if dtype.is_float()
            else "datetime"
            if dtype.is_temporal()
            else "string"
        )
        columns.append(
            {
                "name": name,
                "column_id": f"csv:c{index:04d}",
                "display_name": name,
                "normalized_name": name.casefold(),
                "inferred_type": inferred_type,
                "semantic_type": (
                    "measurement"
                    if inferred_type in {"integer", "number"}
                    else "datetime"
                    if inferred_type == "datetime" or name == "時間"
                    else "category"
                ),
                "null_ratio": 0,
                "distinct_count": frame[name].n_unique(),
                "min": frame[name].min() if inferred_type in {"integer", "number"} else None,
                "max": frame[name].max() if inferred_type in {"integer", "number"} else None,
                "mean": frame[name].mean() if inferred_type in {"integer", "number"} else None,
                "quantiles": {},
                "sample_values": [
                    value.isoformat() if isinstance(value, date) else value
                    for value in frame[name].head(3).to_list()
                ],
            }
        )
    return file_id, {
        "datasets": [
            {
                "key": "csv",
                "sheet": "CSV",
                "path": str(path),
                "row_count": frame.height,
                "columns": columns,
            }
        ]
    }


def _compile(file_id, manifest, inputs, filters=None):
    return AnalysisRecipeCompiler().compile(
        IntentDraft(
            sources=[DatasetSource(file_id=file_id, alias="data", sheet="CSV")],
            recipe_id="trend_summary",
            inputs=inputs,
            filters=filters or [],
        ),
        {str(file_id): manifest},
        allowed_file_ids={str(file_id)},
        plan_origin="llm_intent",
    )


def test_count_rejects_value_field_and_non_count_requires_numeric_value(
    tmp_path: Path,
) -> None:
    file_id, manifest = _manifest(
        tmp_path,
        [{"時間": date(2026, 1, 1), "數據值": 10, "狀態": "OK"}],
    )
    with pytest.raises(APIError) as count_error:
        _compile(
            file_id,
            manifest,
            {
                "date_field": "時間",
                "value_field": "數據值",
                "period": "month",
                "aggregation": "count",
            },
        )
    assert count_error.value.error_code == ErrorCode.RECIPE_PARAMETER_INVALID
    assert count_error.value.details["path"] == "inputs.value_field"

    with pytest.raises(APIError, match="numeric value_field"):
        _compile(
            file_id,
            manifest,
            {
                "date_field": "時間",
                "period": "month",
                "aggregation": "mean",
            },
        )

    with pytest.raises(APIError) as type_error:
        _compile(
            file_id,
            manifest,
            {
                "date_field": "時間",
                "value_field": "狀態",
                "period": "month",
                "aggregation": "mean",
            },
        )
    assert type_error.value.error_code == ErrorCode.COLUMN_TYPE_INCOMPATIBLE


def test_semantic_validation_and_ambiguous_aggregation_clarification(
    tmp_path: Path,
) -> None:
    file_id, manifest = _manifest(
        tmp_path,
        [{"時間": date(2026, 1, 1), "數據值": 10}],
    )
    _intent, plan, _actions, _warnings = _compile(
        file_id,
        manifest,
        {
            "date_field": "時間",
            "value_field": "數據值",
            "period": "month",
            "aggregation": "mean",
        },
    )
    service = AnalysisIntentSemanticService()
    with pytest.raises(APIError) as mismatch:
        service.validate_explicit("按月加總數據值", plan)
    assert mismatch.value.details["requested_aggregation"] == "sum"

    clarification = service.clarification(
        "依月份繪製數據值折線圖",
        plan,
        {str(file_id): manifest},
    )
    assert clarification is not None
    assert clarification.code == "AGGREGATION_REQUIRED"
    assert {item.value for item in clarification.options} >= {"mean", "sum", "count"}
    count_plan = service.apply_choice(plan, clarification, "count")
    assert count_plan.recipe_inputs["aggregation"] == "count"
    assert "value_field" not in count_plan.recipe_inputs
    assert service.explicit_aggregation("show discount trend") is None
    assert service.explicit_aggregation("show mean thickness") == "mean"


def test_schema_context_exposes_full_field_profile(tmp_path: Path) -> None:
    file_id, manifest = _manifest(
        tmp_path,
        [{"時間": date(2026, 1, 1), "數據值": 10}],
    )
    source = AnalysisFile(
        id=file_id,
        workspace_id=uuid4(),
        filename="values.csv",
        original_filename="values.csv",
        file_type="csv",
        file_path="values.csv",
        size_bytes=1,
        confidential_level=ConfidentialLevel.INTERNAL.value,
        status=AnalysisFileStatus.READY.value,
        dataset_manifest=manifest,
    )
    context = json.loads(AnalysisOrchestratorService._schema_context(None, [source]))
    profile = context[0]["datasets"][0]["columns"][1]
    assert profile["semantic_hint"] == "measurement"
    assert profile["min"] == 10
    assert profile["max"] == 10
    assert profile["mean"] == 10
    assert profile["null_rate"] == 0


def test_daily_count_trend_warns_when_values_match_calendar_days(tmp_path: Path) -> None:
    rows = []
    current = date(2026, 1, 1)
    while current < date(2026, 3, 1):
        rows.append({"時間": current})
        current += timedelta(days=1)
    file_id, manifest = _manifest(tmp_path, rows)
    _intent, plan, _actions, _warnings = _compile(
        file_id,
        manifest,
        {
            "date_field": "時間",
            "period": "month",
            "aggregation": "count",
        },
    )
    result = AnalysisRecipeExecutor().execute(plan, {str(file_id): manifest})
    assert [row["value"] for row in result["table"]["rows"]] == [31, 28]
    assert any(
        warning.startswith("COUNT_RESULTS_MATCH_CALENDAR_PERIODS")
        for warning in result["summary"]["warnings"]
    )


def test_between_filter_is_expanded_and_date_strings_execute(tmp_path: Path) -> None:
    file_id, manifest = _manifest(
        tmp_path,
        [
            {"時間": date(2025, 12, 31), "數據值": 1},
            {"時間": date(2026, 1, 1), "數據值": 10},
            {"時間": date(2026, 2, 28), "數據值": 20},
            {"時間": date(2026, 3, 1), "數據值": 30},
        ],
    )
    _intent, plan, actions, _warnings = _compile(
        file_id,
        manifest,
        {
            "date_field": "時間",
            "value_field": "數據值",
            "period": "month",
            "aggregation": "mean",
        },
        filters=[
            {
                "column": "時間",
                "operator": "between",
                "value": ["2026-01-01", "2026-02-28"],
            }
        ],
    )
    assert [condition.operator for condition in plan.filters] == ["gte", "lt"]
    assert plan.filters[1].value == "2026-03-01"
    assert any(action["code"] == "FILTER_BETWEEN_EXPANDED" for action in actions)
    assert any(
        action["code"] == "TEMPORAL_INCLUSIVE_END_NORMALIZED"
        for action in actions
    )

    result = AnalysisRecipeExecutor().execute(plan, {str(file_id): manifest})
    assert result["summary"]["matched_rows"] == 2
    assert result["table"]["rows"] == [
        {"period": "2026-01", "value": 10},
        {"period": "2026-02", "value": 20},
    ]


def test_temporal_filters_support_datetime_timezone_time_and_in() -> None:
    executor = AnalysisRecipeExecutor()

    datetime_frame = pl.DataFrame(
        {
            "data.時間": [
                datetime.fromisoformat("2026-02-28T23:59:00"),
                datetime.fromisoformat("2026-03-01T00:00:00"),
            ]
        }
    )
    filtered = executor._apply_filters(
        datetime_frame,
        [
            FilterCondition(
                column="data.時間",
                operator="gte",
                value="2026-01-01",
            ),
            FilterCondition(
                column="data.時間",
                operator="lt",
                value="2026-03-01",
            ),
        ],
    )
    assert filtered["data.時間"].to_list() == [
        datetime.fromisoformat("2026-02-28T23:59:00")
    ]

    timezone_frame = pl.DataFrame(
        {
            "data.時間": [
                datetime(2026, 2, 28, 23, tzinfo=UTC),
                datetime(2026, 3, 1, 1, tzinfo=UTC),
            ]
        }
    )
    filtered = executor._apply_filters(
        timezone_frame,
        [
            FilterCondition(
                column="data.時間",
                operator="gte",
                value="2026-02-28T23:00:00Z",
            ),
            FilterCondition(
                column="data.時間",
                operator="lt",
                value="2026-03-01T00:00:00+00:00",
            ),
        ],
    )
    assert filtered.height == 1

    date_frame = pl.DataFrame(
        {"data.日期": [date(2026, 1, 1), date(2026, 1, 2)]}
    )
    filtered = executor._apply_filters(
        date_frame,
        [
            FilterCondition(
                column="data.日期",
                operator="in",
                value=["2026-01-02"],
            )
        ],
    )
    assert filtered["data.日期"].to_list() == [date(2026, 1, 2)]

    time_frame = pl.DataFrame(
        {"data.時刻": [time(8, 0), time(12, 30), time(17, 0)]}
    )
    filtered = executor._apply_filters(
        time_frame,
        [
            FilterCondition(
                column="data.時刻",
                operator="gte",
                value="12:30:00",
            )
        ],
    )
    assert filtered["data.時刻"].to_list() == [time(12, 30), time(17, 0)]


def test_invalid_temporal_filter_is_rejected_before_execution(tmp_path: Path) -> None:
    file_id, manifest = _manifest(
        tmp_path,
        [{"時間": date(2026, 1, 1), "數據值": 10}],
    )
    with pytest.raises(APIError) as error:
        _compile(
            file_id,
            manifest,
            {
                "date_field": "時間",
                "value_field": "數據值",
                "period": "month",
                "aggregation": "mean",
            },
            filters=[
                {
                    "column": "時間",
                    "operator": "gte",
                    "value": "2026-02-30",
                }
            ],
        )
    assert error.value.error_code == ErrorCode.RECIPE_PARAMETER_INVALID
    assert error.value.status_code == 422
    assert error.value.details["path"] == "filters[0].value"
    assert error.value.details["expected_format"].startswith("YYYY-MM-DD")


def test_filter_prompts_publish_operator_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "analysis_intent_flow_enabled", True)
    system_prompt, _user_prompt = AnalysisOrchestratorService._planning_prompts(
        question="比較一月和二月",
        schema_context="{}",
    )
    assert "eq/ne/gt/gte/lt/lte/contains/in/is_null/not_null" in system_prompt
    assert "不得使用 between" in system_prompt


class _DraftLLM:
    def __init__(self, file_id, responses: list[dict]) -> None:
        self.file_id = file_id
        self.responses = list(responses)

    async def complete(self, **_kwargs) -> str:
        payload = self.responses.pop(0)
        payload.setdefault(
            "sources",
            [{"file_id": str(self.file_id), "alias": "data", "sheet": "CSV"}],
        )
        return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_intent_flow_repairs_missing_recipe_and_requires_aggregation_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "analysis_intent_flow_enabled", True)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    principal = Principal(
        external_user_id="intent-accuracy-user",
        username="intent-accuracy-user",
        clearance_level=ConfidentialLevel.INTERNAL,
    )
    file_id, manifest = _manifest(
        tmp_path,
        [{"時間": date(2026, 1, 1), "數據值": 10}],
    )
    with Session(engine) as db:
        user = AuditService(db).ensure_user(principal)
        workspace = WorkspaceService(db).get_or_create_personal(principal)
        db.add(
            AnalysisFile(
                id=file_id,
                workspace_id=workspace.id,
                created_by=user.id,
                filename="values.csv",
                original_filename="values.csv",
                file_type="csv",
                file_path=str(tmp_path / "values.csv"),
                dataset_manifest=manifest,
                profile_progress=100,
                status=AnalysisFileStatus.READY.value,
                size_bytes=1,
                confidential_level=ConfidentialLevel.INTERNAL.value,
            )
        )
        db.commit()
        service = AnalysisOrchestratorService(
            db,
            llm_service=_DraftLLM(
                file_id,
                [
                    {"select": ["data.數據值"]},
                    {
                        "recipe_id": "trend_summary",
                        "inputs": {
                            "date_field": "data.時間",
                            "value_field": "data.數據值",
                            "period": "month",
                            "aggregation": "mean",
                        },
                        "filters": [],
                    },
                ],
            ),
        )
        draft = await service.create_draft(
            AnalysisPlanDraftCreate(
                workspace_id=workspace.id,
                question="依月份繪製數據值折線圖",
                file_ids=[file_id],
            ),
            principal,
        )
        assert draft.repair_attempted is True
        assert draft.recipe_id == "trend_summary"
        assert draft.clarification_json["code"] == "AGGREGATION_REQUIRED"
        with pytest.raises(APIError) as clarification_error:
            service.confirm(draft.id, principal)
        assert (
            clarification_error.value.error_code
            == ErrorCode.ANALYSIS_CLARIFICATION_REQUIRED
        )
        job = service.confirm(draft.id, principal, clarification_choice="mean")
        assert job.request_json["recipe_inputs"]["aggregation"] == "mean"


def test_versioned_intent_eval_dataset_and_scoring() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "analysis_intent_eval_cases.json"
    )
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    predictions = [
        {
            "case_id": case["case_id"],
            "intent": {
                "recipe_id": case["expected"]["recipe_id"],
                "inputs": {
                    "aggregation": case["expected"]["aggregation"],
                    "value_field": case["expected"]["value_field"],
                },
            },
            "clarification_required": case["expected"]["clarification_required"],
        }
        for case in cases
    ]
    report = AnalysisIntentQualityService().evaluate(cases, predictions)
    assert report["dataset_version"] == "manufacturing-v1"
    assert report["evaluated_cases"] == len(cases)
    assert report["exact_match_rate"] == 1
