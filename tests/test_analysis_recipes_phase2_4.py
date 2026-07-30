import json
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.constants import AnalysisFileStatus, ConfidentialLevel, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.base import Base
from app.models.analysis import AnalysisFile
from app.models.chat import ChatSession
from app.schemas.analysis import AnalysisPlan, AnalysisPlanDraftCreate, DatasetSource
from app.schemas.analysis_recipe import IntentDraft
from app.services.analysis_orchestrator_service import AnalysisOrchestratorService
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler
from app.services.analysis_recipe_executor import AnalysisRecipeExecutor
from app.services.analysis_recipe_registry import AnalysisRecipeRegistry
from app.services.audit_service import AuditService
from app.services.dataset_query_service import DatasetQueryService
from app.services.spreadsheet_ingestion_service import SpreadsheetIngestionService
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage


def _manifest(tmp_path: Path, rows: list[dict], file_id, *, alias: str = "data"):
    path = tmp_path / f"{file_id}.parquet"
    pl.DataFrame(rows).write_parquet(path)
    dataframe = pl.read_parquet(path)
    columns = []
    for index, (name, dtype) in enumerate(dataframe.schema.items(), start=1):
        columns.append(
            {
                "name": name,
                "column_id": f"csv:c{index:04d}",
                "display_name": name,
                "normalized_name": name.lower(),
                "inferred_type": SpreadsheetIngestionService._polars_type(dtype),
            }
        )
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
    source = DatasetSource(file_id=file_id, alias=alias, sheet="CSV")
    return source, manifest


def _execute(tmp_path: Path, recipe_id: str, inputs: dict, rows: list[dict]):
    file_id = uuid4()
    source, manifest = _manifest(tmp_path, rows, file_id)
    intent = IntentDraft(
        sources=[source],
        recipe_id=recipe_id,
        inputs=inputs,
    )
    normalized, plan, actions, warnings = AnalysisRecipeCompiler().compile(
        intent,
        {str(file_id): manifest},
        allowed_file_ids={str(file_id)},
    )
    assert normalized.recipe_id == recipe_id
    assert warnings == []
    result = AnalysisRecipeExecutor().execute(plan, {str(file_id): manifest})
    return result, actions


def test_registry_is_versioned_and_rejects_undeclared_parameters() -> None:
    registry = AnalysisRecipeRegistry()
    assert registry.get("histogram", "1.0").result_schema == [
        "bin_start",
        "bin_end",
        "bin_label",
        "record_count",
    ]
    assert {"histogram", "pareto", "join_compare"}.issubset(
        {item.recipe_id for item in registry.list_enabled()}
    )
    with pytest.raises(APIError) as exc_info:
        registry.normalize_inputs(
            registry.get("histogram"),
            {"source_field": "x", "bin_width": 10, "python": "open('/etc/passwd')"},
        )
    assert exc_info.value.error_code == ErrorCode.RECIPE_PARAMETER_INVALID


def test_histogram_handles_negative_boundary_null_and_chart_contract(tmp_path: Path) -> None:
    result, _actions = _execute(
        tmp_path,
        "histogram",
        {"source_field": "value", "bin_width": "10"},
        [
            {"value": -10.0},
            {"value": -0.1},
            {"value": 0.0},
            {"value": 9.99},
            {"value": 10.0},
            {"value": None},
        ],
    )
    assert result["schema_version"] == "3.0"
    assert result["summary"]["null_count"] == 1
    assert sum(row["record_count"] for row in result["table"]["rows"]) == 5
    assert result["table"]["rows"][0]["bin_start"] == -10
    assert result["table"]["rows"][-1]["record_count"] == 3
    chart = result["charts"][0]
    assert chart["semantic_type"] == "histogram"
    assert chart["x_field"] == "bin_label"
    assert chart["y_field"] == "record_count"


def test_histogram_empty_and_bin_limit_are_deterministic(tmp_path: Path) -> None:
    result, _actions = _execute(
        tmp_path,
        "histogram",
        {"source_field": "value", "bin_width": 5},
        [{"value": None}, {"value": None}],
    )
    assert result["table"]["rows"] == []
    assert result["summary"]["null_count"] == 2
    with pytest.raises(APIError) as exc_info:
        _execute(
            tmp_path,
            "histogram",
            {"source_field": "value", "bin_width": 1, "start": 0, "end": 201},
            [{"value": 1}],
        )
    assert exc_info.value.error_code == ErrorCode.RECIPE_PARAMETER_INVALID


def test_phase3_summary_and_pareto_recipes(tmp_path: Path) -> None:
    category, _ = _execute(
        tmp_path,
        "category_summary",
        {
            "category_field": "machine",
            "value_field": "yield_value",
            "aggregation": "mean",
        },
        [
            {"machine": "A", "yield_value": 90},
            {"machine": "A", "yield_value": 100},
            {"machine": "B", "yield_value": 80},
        ],
    )
    assert category["table"]["rows"] == [
        {"category": "A", "value": 95.0},
        {"category": "B", "value": 80.0},
    ]
    assert category["charts"][0]["semantic_type"] == "category_bar"

    pareto, _ = _execute(
        tmp_path,
        "pareto",
        {"category_field": "reason"},
        [{"reason": "scratch"}, {"reason": "scratch"}, {"reason": "dust"}],
    )
    assert pareto["table"]["rows"][-1]["cumulative_ratio"] == 1
    assert pareto["charts"][0]["chart_type"] == "composite"


def test_phase4_column_id_and_transform_recipes(tmp_path: Path) -> None:
    file_id = uuid4()
    source, manifest = _manifest(
        tmp_path,
        [{"A": 10, "B": 2}, {"A": 3, "B": 0}],
        file_id,
    )
    intent = IntentDraft(
        sources=[source],
        recipe_id="derive_arithmetic",
        inputs={
            "left_field": "csv:c0001",
            "right_field": "csv:c0002",
            "operation": "divide",
        },
    )
    normalized, plan, _actions, _warnings = AnalysisRecipeCompiler().compile(
        intent,
        {str(file_id): manifest},
        allowed_file_ids={str(file_id)},
    )
    assert normalized.inputs["left_field"] == "data.A"
    result = AnalysisRecipeExecutor().execute(plan, {str(file_id): manifest})
    assert result["table"]["rows"] == [
        {"derived_value": 5},
        {"derived_value": None},
    ]
    assert result["summary"]["division_by_zero_count"] == 1


def test_missing_duplicate_date_parts_and_join_compare(tmp_path: Path) -> None:
    missing, _ = _execute(
        tmp_path,
        "missing_value_summary",
        {"fields": ["value"]},
        [{"value": 1}, {"value": None}],
    )
    assert missing["table"]["rows"][0]["null_ratio"] == 0.5

    duplicate, _ = _execute(
        tmp_path,
        "duplicate_summary",
        {"key_fields": ["id"]},
        [{"id": "A"}, {"id": "A"}, {"id": "B"}],
    )
    assert duplicate["table"]["rows"] == [{"key": "A", "duplicate_count": 2}]

    date_parts, _ = _execute(
        tmp_path,
        "date_parts",
        {"source_field": "created_at"},
        [{"created_at": "2026-07-31"}, {"created_at": "invalid"}],
    )
    assert date_parts["table"]["rows"][0]["quarter"] == 3
    assert date_parts["summary"]["parse_failure_count"] == 1

    left_id = uuid4()
    right_id = uuid4()
    left_source, left_manifest = _manifest(
        tmp_path,
        [{"id": "A"}, {"id": "B"}],
        left_id,
        alias="left",
    )
    right_source, right_manifest = _manifest(
        tmp_path,
        [{"id": "B"}, {"id": "C"}],
        right_id,
        alias="right",
    )
    intent = IntentDraft(
        sources=[left_source, right_source],
        recipe_id="join_compare",
        inputs={"left_key": "left.id", "right_key": "right.id"},
    )
    _normalized, plan, _actions, _warnings = AnalysisRecipeCompiler().compile(
        intent,
        {str(left_id): left_manifest, str(right_id): right_manifest},
        allowed_file_ids={str(left_id), str(right_id)},
    )
    result = AnalysisRecipeExecutor().execute(
        plan,
        {str(left_id): left_manifest, str(right_id): right_manifest},
    )
    assert {row["match_status"] for row in result["table"]["rows"]} == {
        "left_only",
        "matched",
        "right_only",
    }


def test_profile_contains_stable_ids_full_scope_and_numeric_statistics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profile.csv"
    path.write_text("Thickness,Status\n10,OK\n20,\n", encoding="utf-8")
    result = SpreadsheetIngestionService(
        WorkspaceStorage(root=str(tmp_path / "storage"))
    ).profile_and_convert(
        workspace_id=uuid4(),
        file_id=uuid4(),
        file_path=str(path),
        file_type="csv",
    )
    column = result["dataset_manifest"]["datasets"][0]["columns"][0]
    assert column["column_id"] == "csv:c0001"
    assert column["display_name"] == "Thickness"
    assert column["profile_scope"] == "full"
    assert column["null_ratio"] == 0
    assert column["distinct_count"] == 2
    assert column["mean"] == 15
    assert column["quantiles"]["p50"] == 15


class _RecipeLLM:
    def __init__(self, file_id) -> None:
        self.file_id = file_id

    async def complete(self, **_kwargs) -> str:
        return json.dumps(
            {
                "schema_version": "1.0",
                "sources": [
                    {
                        "file_id": str(self.file_id),
                        "alias": "data",
                        "sheet": "CSV",
                    }
                ],
                "recipe_id": "histogram",
                "recipe_version": "1.0",
                "inputs": {"source_field": "Thickness", "bin_width": 10},
                "filters": [],
                "chart_enabled": True,
                "title": "Thickness distribution",
            }
        )


@pytest.mark.asyncio
async def test_natural_language_histogram_draft_confirm_and_execute(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    principal = Principal(
        external_user_id="recipe-user",
        username="recipe-user",
        clearance_level=ConfidentialLevel.INTERNAL,
    )
    file_id = uuid4()
    _source, manifest = _manifest(
        tmp_path,
        [{"Thickness": -1}, {"Thickness": 0}, {"Thickness": 10}],
        file_id,
    )
    with Session(engine) as db:
        user = AuditService(db).ensure_user(principal)
        workspace = WorkspaceService(db).get_or_create_personal(principal)
        session = ChatSession(user_id=user.id, workspace_id=workspace.id)
        db.add(session)
        db.flush()
        source = AnalysisFile(
            id=file_id,
            workspace_id=workspace.id,
            session_id=session.id,
            created_by=user.id,
            filename="thickness.csv",
            original_filename="thickness.csv",
            file_type="csv",
            file_path=str(tmp_path / "thickness.csv"),
            dataset_manifest=manifest,
            profile_progress=100,
            status=AnalysisFileStatus.READY.value,
            size_bytes=1,
            confidential_level=ConfidentialLevel.INTERNAL.value,
        )
        db.add(source)
        db.commit()

        service = AnalysisOrchestratorService(db, llm_service=_RecipeLLM(file_id))
        draft = await service.create_draft(
            AnalysisPlanDraftCreate(
                workspace_id=workspace.id,
                session_id=session.id,
                question="將厚度每 10 為區間統計數量並繪製直方圖",
                file_ids=[file_id],
            ),
            principal,
        )
        assert draft.plan_json["recipe_id"] == "histogram"
        assert draft.plan_json["charts"] == []
        job = service.confirm(draft.id, principal)
        result = DatasetQueryService().execute(
            AnalysisPlan.model_validate(job.request_json),
            {str(file_id): manifest},
        )
        assert result["summary"]["recipe_id"] == "histogram"
        assert result["charts"][0]["x_field"] == "bin_label"
        assert result["charts"][0]["y_field"] == "record_count"
