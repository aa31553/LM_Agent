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
        "period_overlay": ("period_overlay", "line", "day", "value"),
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
        "yield_summary": ("category_bar", "bar", "status", "count"),
        "spec_judgement": ("category_bar", "bar", "status", "count"),
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
                    "field_semantics": {
                        "category": {"semantic_type": "Category", "data_type": "nominal"},
                        "count": {"semantic_type": "Quantity", "data_type": "quantitative"},
                        "cumulative_ratio": {
                            "semantic_type": "Percentage",
                            "data_type": "quantitative",
                            "intrinsic_domain": [0, 1],
                        },
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
        if recipe_id == "boxplot_summary":
            required = {"group", "min", "q1", "median", "q3", "max", "outliers"}
            self._require_fields(limited, required, recipe_id)
            return [
                {
                    "schema_version": "3.0",
                    "semantic_type": "boxplot",
                    "chart_type": "boxplot",
                    "title": title or "Boxplot",
                    "fields": {
                        "group": "group",
                        "min": "min",
                        "q1": "q1",
                        "median": "median",
                        "q3": "q3",
                        "max": "max",
                        "outliers": "outliers",
                    },
                    "field_semantics": {
                        "group": {"semantic_type": "Category", "data_type": "nominal"},
                        "median": {"semantic_type": "Quantity", "data_type": "quantitative"},
                    },
                    "interaction": {"tooltip": True, "zoom": True, "export": True},
                    "data": limited,
                    "truncated": len(rows) > len(limited),
                }
            ], warnings
        if recipe_id == "correlation_matrix":
            required = {"x_category", "y_category", "value", "sample_size"}
            self._require_fields(limited, required, recipe_id)
            return [
                {
                    "schema_version": "3.0",
                    "semantic_type": "heatmap",
                    "chart_type": "heatmap",
                    "title": title or "Correlation matrix",
                    "fields": {
                        "x": "x_category",
                        "y": "y_category",
                        "value": "value",
                        "sample_size": "sample_size",
                    },
                    "field_semantics": {
                        "x_category": {"semantic_type": "Category", "data_type": "nominal"},
                        "y_category": {"semantic_type": "Category", "data_type": "nominal"},
                        "value": {
                            "semantic_type": "Correlation",
                            "data_type": "quantitative",
                            "intrinsic_domain": [-1, 1],
                        },
                    },
                    "interaction": {"tooltip": True, "zoom": False, "export": True},
                    "data": limited,
                    "truncated": len(rows) > len(limited),
                }
            ], warnings
        if recipe_id == "control_chart":
            required = {"period", "value", "center_line", "ucl", "lcl"}
            self._require_fields(limited, required, recipe_id)
            return [
                {
                    "schema_version": "3.0",
                    "semantic_type": "control_chart",
                    "chart_type": "line",
                    "type": "line",
                    "title": title or "Control chart",
                    "x_field": "period",
                    "y_field": "value",
                    "fields": {
                        "period": "period",
                        "value": "value",
                        "center_line": "center_line",
                        "ucl": "ucl",
                        "lcl": "lcl",
                    },
                    "field_semantics": {
                        "period": {"semantic_type": "Sequence", "data_type": "ordinal"},
                        "value": {"semantic_type": "Quantity", "data_type": "quantitative"},
                    },
                    "x_type": "category",
                    "interaction": {"tooltip": True, "zoom": True, "export": True},
                    "data": limited,
                    "truncated": len(rows) > len(limited),
                }
            ], warnings
        if recipe_id == "process_capability":
            required = {"cp", "cpk", "pp", "ppk", "lsl", "usl", "mean"}
            self._require_fields(limited, required, recipe_id)
            return [
                {
                    "schema_version": "3.0",
                    "semantic_type": "spec_capability",
                    "chart_type": "bar",
                    "title": title or "Process capability",
                    "fields": {
                        "cp": "cp",
                        "cpk": "cpk",
                        "pp": "pp",
                        "ppk": "ppk",
                        "lsl": "lsl",
                        "usl": "usl",
                        "mean": "mean",
                    },
                    "field_semantics": {
                        "cp": {"semantic_type": "Quantity", "data_type": "quantitative"},
                        "cpk": {"semantic_type": "Quantity", "data_type": "quantitative"},
                        "pp": {"semantic_type": "Quantity", "data_type": "quantitative"},
                        "ppk": {"semantic_type": "Quantity", "data_type": "quantitative"},
                    },
                    "interaction": {"tooltip": True, "zoom": False, "export": True},
                    "data": limited,
                    "truncated": len(rows) > len(limited),
                }
            ], warnings
        configuration = self._STANDARD.get(recipe_id)
        if configuration is None:
            return [], warnings
        semantic_type, chart_type, x_field, y_field = configuration
        self._require_fields(limited, {x_field, y_field}, recipe_id)
        configured_series_field = (
            "series"
            if recipe_id in {"trend_summary", "period_overlay"}
            else None
        )
        series_field = (
            configured_series_field
            if configured_series_field
            and (
                recipe_id == "period_overlay"
                or any(configured_series_field in row for row in limited)
            )
            else None
        )
        if series_field:
            self._require_fields(limited, {series_field}, recipe_id)
        x_type = (
            "time"
            if recipe_id == "trend_summary"
            else "value"
            if recipe_id == "period_overlay"
            else "category"
        )
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
                "field_semantics": {
                    x_field: {
                        "semantic_type": (
                            "Date" if x_type == "time" else "Quantity" if x_type == "value" else "Category"
                        ),
                        "data_type": (
                            "temporal" if x_type == "time" else "quantitative" if x_type == "value" else "nominal"
                        ),
                    },
                    y_field: {"semantic_type": "Quantity", "data_type": "quantitative"},
                    **(
                        {series_field: {"semantic_type": "Category", "data_type": "nominal"}}
                        if series_field
                        else {}
                    ),
                },
                "x_type": x_type,
                "axis": {
                    "x_type": x_type,
                    "x_label": (
                        "day of month" if recipe_id == "period_overlay" else x_field
                    ),
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
