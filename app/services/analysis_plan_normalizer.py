from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.core.constants import ErrorCode
from app.core.exceptions import APIError


@dataclass(frozen=True)
class AnalysisPlanNormalizationResult:
    payload: dict[str, Any]
    actions: list[dict[str, Any]]


class AnalysisPlanNormalizer:
    """Normalize legacy, unambiguous AnalysisPlan fields before Pydantic validation."""

    def normalize(self, payload: dict[str, Any]) -> AnalysisPlanNormalizationResult:
        if not isinstance(payload, dict):
            raise APIError(
                ErrorCode.ANALYSIS_INTENT_INVALID,
                "The analysis plan must be a JSON object.",
                422,
            )

        normalized = deepcopy(payload)
        actions: list[dict[str, Any]] = []
        if "plan" in normalized and isinstance(normalized["plan"], dict):
            normalized = deepcopy(normalized["plan"])
            actions.append(self._action("PLAN_WRAPPER_REMOVED", "plan"))

        charts = normalized.get("charts")
        if charts is None:
            normalized["charts"] = []
            charts = normalized["charts"]
        if not isinstance(charts, list):
            return AnalysisPlanNormalizationResult(normalized, actions)

        for index, chart in enumerate(charts):
            if not isinstance(chart, dict):
                continue
            path = f"charts[{index}]"
            self._map_chart_field(
                chart,
                legacy_field="x",
                target_field="x_field",
                path=path,
                actions=actions,
            )
            self._map_chart_field(
                chart,
                legacy_field="y",
                target_field="y_field",
                path=path,
                actions=actions,
            )

        return AnalysisPlanNormalizationResult(normalized, actions)

    def _map_chart_field(
        self,
        chart: dict[str, Any],
        *,
        legacy_field: str,
        target_field: str,
        path: str,
        actions: list[dict[str, Any]],
    ) -> None:
        if legacy_field not in chart:
            return

        legacy_value = chart[legacy_field]
        if legacy_value is None:
            raise APIError(
                ErrorCode.ANALYSIS_INTENT_INVALID,
                f"{path}.{legacy_field} cannot be null; the field must be explicit.",
                422,
                details={"path": f"{path}.{legacy_field}"},
            )
        if legacy_field == "y" and isinstance(legacy_value, list):
            if len(legacy_value) != 1 or not isinstance(legacy_value[0], str):
                raise APIError(
                    ErrorCode.ANALYSIS_INTENT_INVALID,
                    f"{path}.y must contain exactly one string value.",
                    422,
                    details={"path": f"{path}.y"},
                )
            legacy_value = legacy_value[0]
        if not isinstance(legacy_value, str) or not legacy_value.strip():
            raise APIError(
                ErrorCode.ANALYSIS_INTENT_INVALID,
                f"{path}.{legacy_field} must be a non-empty string.",
                422,
                details={"path": f"{path}.{legacy_field}"},
            )

        current_value = chart.get(target_field)
        if current_value is not None and current_value != legacy_value:
            raise APIError(
                ErrorCode.AMBIGUOUS_CHART_FIELD,
                f"{path}.{legacy_field} conflicts with {path}.{target_field}.",
                422,
                details={
                    "path": path,
                    "legacy_field": legacy_field,
                    "target_field": target_field,
                },
            )

        chart[target_field] = legacy_value
        del chart[legacy_field]
        code = (
            "LEGACY_CHART_FIELD_DEDUPLICATED"
            if current_value == legacy_value
            else f"LEGACY_CHART_{legacy_field.upper()}_MAPPED"
        )
        actions.append(
            self._action(
                code,
                path,
                source_field=legacy_field,
                target_field=target_field,
            )
        )

    @staticmethod
    def _action(
        code: str,
        path: str,
        *,
        source_field: str | None = None,
        target_field: str | None = None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "path": path,
            "source_field": source_field,
            "target_field": target_field,
        }
