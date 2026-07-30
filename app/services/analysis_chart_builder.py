from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError


class AnalysisChartBuilder:
    """Build trusted Chart Schema v3 objects from validated recipe results."""

    _STANDARD = {
        "histogram": ("histogram", "bar", "bin_label", "record_count"),
        "category_summary": ("category_bar", "bar", "category", "value"),
        "trend_summary": ("trend_line", "line", "period", "value"),
        "data_quality_summary": (
            "data_quality",
            "bar",
            "column",
            "null_count",
        ),
        "group_summary": ("category_bar", "bar", "group", "value"),
        "missing_value_summary": (
            "missing_values",
            "bar",
            "column",
            "null_count",
        ),
    }

    def build(
        self,
        recipe_id: str,
        rows: list[dict[str, Any]],
        *,
        title: str | None = None,
        enabled: bool = True,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not enabled:
            return [], []
        limited = rows[: settings.analysis_chart_max_points]
        warnings = (
            ["Chart data was truncated to the configured point limit."]
            if len(rows) > len(limited)
            else []
        )
        if recipe_id == "pareto":
            self._require_fields(
                limited,
                {"category", "count", "cumulative_ratio"},
                recipe_id,
            )
            return [
                {
                    "schema_version": "3.0",
                    "semantic_type": "pareto",
                    "chart_type": "composite",
                    "title": title or "Pareto",
                    "fields": {
                        "category": "category",
                        "bar": "count",
                        "line": "cumulative_ratio",
                    },
                    "axis": {
                        "x_type": "category",
                        "x_label": "category",
                        "y_label": "count",
                        "secondary_y_label": "cumulative ratio",
                    },
                    "interaction": {"tooltip": True, "zoom": True, "export": True},
                    "data": limited,
                    "truncated": len(rows) > len(limited),
                }
            ], warnings
        configuration = self._STANDARD.get(recipe_id)
        if configuration is None:
            return [], warnings
        semantic_type, chart_type, x_field, y_field = configuration
        self._require_fields(limited, {x_field, y_field}, recipe_id)
        series_field = "series" if any("series" in row for row in limited) else None
        return [
            {
                "schema_version": "3.0",
                "semantic_type": semantic_type,
                "chart_type": chart_type,
                "type": chart_type,
                "title": title or recipe_id.replace("_", " ").title(),
                "x_field": x_field,
                "y_field": y_field,
                "series_field": series_field,
                "tooltip_fields": [],
                "x_type": "time" if recipe_id == "trend_summary" else "category",
                "axis": {
                    "x_type": "time" if recipe_id == "trend_summary" else "category",
                    "x_label": x_field,
                    "y_label": y_field,
                },
                "format": {"y_unit": None, "decimal_places": 4},
                "interaction": {"tooltip": True, "zoom": True, "export": True},
                "data": limited,
                "truncated": len(rows) > len(limited),
            }
        ], warnings

    @staticmethod
    def _require_fields(
        rows: list[dict[str, Any]],
        fields: set[str],
        recipe_id: str,
    ) -> None:
        if not rows:
            return
        missing = sorted(fields - set(rows[0]))
        if missing:
            raise APIError(
                ErrorCode.CHART_BUILD_FAILED,
                "Recipe result cannot be mapped to its chart contract.",
                500,
                details={"recipe_id": recipe_id, "missing_fields": missing},
            )
