import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.schemas.analysis import AnalysisPlan
from app.services.analysis_orchestrator_service import AnalysisOrchestratorService
from app.services.analysis_plan_normalizer import AnalysisPlanNormalizer


FILE_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_SOURCE = {
    "file_id": FILE_ID,
    "alias": "data",
    "sheet": "Production",
}
MANIFESTS = {
    FILE_ID: {
        "datasets": [
            {
                "sheet": "Production",
                "columns": [
                    {"name": "Machine", "inferred_type": "string"},
                    {"name": "mean_yield", "inferred_type": "number"},
                ],
                "warnings": [],
            }
        ]
    }
}


class FakeRepairLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return self.response


def _legacy_plan() -> dict:
    return {
        "select": ["Machine", "mean_yield"],
        "charts": [
            {
                "type": "bar",
                "x": "Machine",
                "y": ["mean_yield"],
            }
        ],
    }


def test_phase0_reproduces_legacy_chart_contract_failure() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(_legacy_plan())


def test_normalizer_maps_legacy_chart_fields_without_mutating_input() -> None:
    payload = _legacy_plan()
    original = deepcopy(payload)

    result = AnalysisPlanNormalizer().normalize(payload)
    plan = AnalysisPlan.model_validate(result.payload)

    assert payload == original
    assert plan.charts[0].x_field == "Machine"
    assert plan.charts[0].y_field == "mean_yield"
    assert [action["code"] for action in result.actions] == [
        "LEGACY_CHART_X_MAPPED",
        "LEGACY_CHART_Y_MAPPED",
    ]


def test_normalizer_rejects_conflicting_chart_contracts() -> None:
    payload = _legacy_plan()
    payload["charts"][0]["x_field"] = "Batch"

    with pytest.raises(APIError) as error:
        AnalysisPlanNormalizer().normalize(payload)

    assert error.value.error_code == ErrorCode.AMBIGUOUS_CHART_FIELD
    assert error.value.details["path"] == "charts[0]"


@pytest.mark.parametrize(
    ("legacy_field", "legacy_value"),
    [("x", None), ("y", None), ("y", ["a", "b"])],
)
def test_normalizer_does_not_guess_ambiguous_or_null_fields(
    legacy_field: str,
    legacy_value,
) -> None:
    payload = _legacy_plan()
    payload["charts"][0][legacy_field] = legacy_value

    with pytest.raises(APIError) as error:
        AnalysisPlanNormalizer().normalize(payload)

    assert error.value.error_code == ErrorCode.ANALYSIS_INTENT_INVALID


def test_orchestrator_repairs_invalid_plan_at_most_once() -> None:
    llm = FakeRepairLLM('{"select":["Machine"],"limit":100,"charts":[]}')
    service = AnalysisOrchestratorService(None, llm_service=llm)

    plan, warnings, actions, attempted, errors = asyncio.run(
        service._normalize_and_validate_plan(
            {"select": ["Machine"], "limit": "invalid", "charts": []},
            schema_context='{"columns":["Machine"]}',
            default_source=DEFAULT_SOURCE,
            manifests=MANIFESTS,
            allowed_file_ids={FILE_ID},
        )
    )

    assert plan.limit == 100
    assert warnings == []
    assert attempted is True
    assert errors
    assert actions[-1]["code"] == "LLM_PLAN_REPAIRED"
    assert len(llm.calls) == 1
    assert "禁止輸出 Python" in llm.calls[0]["system_prompt"]



def test_orchestrator_repairs_dataset_validation_error_once() -> None:
    llm = FakeRepairLLM('{"select":["Machine"],"limit":100,"charts":[]}')
    service = AnalysisOrchestratorService(None, llm_service=llm)

    plan, warnings, actions, attempted, errors = asyncio.run(
        service._normalize_and_validate_plan(
            {"select": ["MissingColumn"], "charts": []},
            schema_context='{"columns":["Machine"]}',
            default_source=DEFAULT_SOURCE,
            manifests=MANIFESTS,
            allowed_file_ids={FILE_ID},
        )
    )

    assert plan.select == ["Machine"]
    assert warnings == []
    assert attempted is True
    assert errors == ["Analysis plan references columns that are not present."]
    assert actions[-1]["code"] == "LLM_PLAN_REPAIRED"
    assert len(llm.calls) == 1


def test_orchestrator_stops_after_failed_repair() -> None:
    llm = FakeRepairLLM('{"limit":"still-invalid","charts":[]}')
    service = AnalysisOrchestratorService(None, llm_service=llm)

    with pytest.raises(APIError) as error:
        asyncio.run(
            service._normalize_and_validate_plan(
                {"select": ["Machine"], "limit": "invalid", "charts": []},
                schema_context='{"columns":["Machine"]}',
                default_source=DEFAULT_SOURCE,
                manifests=MANIFESTS,
                allowed_file_ids={FILE_ID},
            )
        )

    assert error.value.error_code == ErrorCode.ANALYSIS_REPAIR_FAILED
    assert len(llm.calls) == 1
    assert "initial_validation_error" in error.value.details
    assert "repair_validation_error" in error.value.details


def test_normalizer_is_independent_of_windows_path_and_shell_semantics() -> None:
    payload = _legacy_plan()
    payload["charts"][0]["title"] = r"C:\LM_Agent\reports\yield"

    result = AnalysisPlanNormalizer().normalize(payload)

    assert result.payload["charts"][0]["title"] == r"C:\LM_Agent\reports\yield"
