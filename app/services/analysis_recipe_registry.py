from __future__ import annotations

from typing import Any

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.schemas.analysis_recipe import RecipeDefinition, RecipeParameter


def _column(required: bool = True, description: str = "") -> RecipeParameter:
    return RecipeParameter(type="column", required=required, description=description)


def _columns(required: bool = True, description: str = "") -> RecipeParameter:
    return RecipeParameter(type="columns", required=required, description=description)


class AnalysisRecipeRegistry:
    """Versioned allow-list of deterministic analysis operations."""

    def __init__(self) -> None:
        self._definitions = {
            (item.recipe_id, item.version): item
            for item in [
                RecipeDefinition(
                    recipe_id="histogram",
                    version="1.0",
                    category="distribution",
                    description="Count a numeric field in fixed-width or fixed-count bins.",
                    parameters={
                        "source_field": _column(description="Numeric field to bin."),
                        "bin_width": RecipeParameter(type="number", minimum=0),
                        "bin_count": RecipeParameter(
                            type="integer", minimum=1, maximum=200
                        ),
                        "start": RecipeParameter(type="number"),
                        "end": RecipeParameter(type="number"),
                        "include_null_count": RecipeParameter(
                            type="boolean", default=True
                        ),
                        "include_underflow": RecipeParameter(
                            type="boolean", default=False
                        ),
                        "include_overflow": RecipeParameter(
                            type="boolean", default=False
                        ),
                        "closed": RecipeParameter(
                            type="enum", default="left", choices=["left"]
                        ),
                    },
                    result_schema=[
                        "bin_start",
                        "bin_end",
                        "bin_label",
                        "record_count",
                    ],
                    chart_semantic_type="histogram",
                ),
                RecipeDefinition(
                    recipe_id="category_summary",
                    version="1.0",
                    category="summary",
                    description="Aggregate a value by category.",
                    parameters={
                        "category_field": _column(),
                        "value_field": _column(required=False),
                        "aggregation": RecipeParameter(
                            type="enum",
                            default="count",
                            choices=["count", "sum", "mean", "median", "min", "max"],
                        ),
                    },
                    result_schema=["category", "value"],
                    chart_semantic_type="category_bar",
                ),
                RecipeDefinition(
                    recipe_id="trend_summary",
                    version="1.0",
                    category="summary",
                    description="Aggregate a value over deterministic time periods.",
                    parameters={
                        "date_field": _column(),
                        "value_field": _column(required=False),
                        "series_field": _column(required=False),
                        "period": RecipeParameter(
                            type="enum",
                            default="month",
                            choices=["day", "week", "month", "quarter", "year"],
                        ),
                        "aggregation": RecipeParameter(
                            type="enum",
                            default="count",
                            choices=["count", "sum", "mean", "median", "min", "max"],
                        ),
                    },
                    result_schema=["period", "value"],
                    chart_semantic_type="trend_line",
                ),
                RecipeDefinition(
                    recipe_id="pareto",
                    version="1.0",
                    category="quality",
                    description="Rank categories and calculate ratio and cumulative ratio.",
                    parameters={"category_field": _column()},
                    result_schema=["category", "count", "ratio", "cumulative_ratio"],
                    chart_semantic_type="pareto",
                ),
                RecipeDefinition(
                    recipe_id="data_quality_summary",
                    version="1.0",
                    category="quality",
                    description="Summarize nulls, distinct values and inferred types.",
                    parameters={"fields": _columns(required=False)},
                    result_schema=[
                        "column",
                        "inferred_type",
                        "null_count",
                        "null_ratio",
                        "distinct_count",
                    ],
                    chart_semantic_type="data_quality",
                ),
                RecipeDefinition(
                    recipe_id="group_summary",
                    version="1.0",
                    category="summary",
                    description="Aggregate a metric by one or more group fields.",
                    parameters={
                        "group_fields": _columns(),
                        "value_field": _column(required=False),
                        "aggregation": RecipeParameter(
                            type="enum",
                            default="count",
                            choices=[
                                "count",
                                "sum",
                                "mean",
                                "median",
                                "std",
                                "min",
                                "max",
                            ],
                        ),
                    },
                    result_schema=["group", "value"],
                    chart_semantic_type="category_bar",
                ),
                RecipeDefinition(
                    recipe_id="missing_value_summary",
                    version="1.0",
                    category="data_quality",
                    description="Count missing values for selected fields.",
                    parameters={"fields": _columns(required=False)},
                    result_schema=["column", "null_count", "null_ratio"],
                    chart_semantic_type="missing_values",
                ),
                RecipeDefinition(
                    recipe_id="duplicate_summary",
                    version="1.0",
                    category="data_quality",
                    description="Find duplicate key combinations.",
                    parameters={"key_fields": _columns()},
                    result_schema=["key", "duplicate_count"],
                ),
                RecipeDefinition(
                    recipe_id="type_cast",
                    version="1.0",
                    category="transform",
                    description="Safely cast one field and report failed conversions.",
                    parameters={
                        "source_field": _column(),
                        "target_type": RecipeParameter(
                            type="enum",
                            required=True,
                            choices=[
                                "string",
                                "integer",
                                "number",
                                "boolean",
                                "date",
                                "datetime",
                            ],
                        ),
                    },
                    result_schema=["source_value", "cast_value", "cast_failed"],
                ),
                RecipeDefinition(
                    recipe_id="date_parts",
                    version="1.0",
                    category="transform",
                    description="Derive controlled date parts from one field.",
                    parameters={"source_field": _column()},
                    result_schema=[
                        "source_value",
                        "year",
                        "quarter",
                        "month",
                        "week",
                        "day",
                    ],
                ),
                RecipeDefinition(
                    recipe_id="derive_arithmetic",
                    version="1.0",
                    category="transform",
                    description="Apply an allow-listed arithmetic template to two fields.",
                    parameters={
                        "left_field": _column(),
                        "right_field": _column(),
                        "operation": RecipeParameter(
                            type="enum",
                            required=True,
                            choices=["add", "subtract", "multiply", "divide", "ratio"],
                        ),
                    },
                    result_schema=["derived_value"],
                ),
                RecipeDefinition(
                    recipe_id="pivot_table",
                    version="1.0",
                    category="reshape",
                    description="Build a bounded pivot table.",
                    parameters={
                        "index_fields": _columns(),
                        "column_field": _column(),
                        "value_field": _column(),
                        "aggregation": RecipeParameter(
                            type="enum",
                            default="sum",
                            choices=["count", "sum", "mean", "min", "max"],
                        ),
                    },
                    result_schema=[],
                ),
                RecipeDefinition(
                    recipe_id="join_compare",
                    version="1.0",
                    category="compare",
                    description="Compare key presence across two approved datasets.",
                    parameters={
                        "left_key": _column(),
                        "right_key": _column(),
                    },
                    result_schema=["key", "left_count", "right_count", "match_status"],
                ),
            ]
        }

    def list_enabled(self) -> list[RecipeDefinition]:
        return sorted(
            (item for item in self._definitions.values() if item.enabled),
            key=lambda item: (item.category, item.recipe_id, item.version),
        )

    def get(self, recipe_id: str, version: str = "1.0") -> RecipeDefinition:
        item = self._definitions.get((recipe_id, version))
        if item is None:
            versions = sorted(
                candidate.version
                for candidate in self._definitions.values()
                if candidate.recipe_id == recipe_id
            )
            code = (
                ErrorCode.RECIPE_VERSION_UNSUPPORTED
                if versions
                else ErrorCode.RECIPE_NOT_FOUND
            )
            raise APIError(
                code,
                "Recipe or recipe version is not available.",
                404,
                details={
                    "recipe_id": recipe_id,
                    "recipe_version": version,
                    "available_versions": versions,
                },
            )
        if not item.enabled:
            raise APIError(
                ErrorCode.RECIPE_NOT_FOUND,
                "Recipe is disabled.",
                404,
                details={"recipe_id": recipe_id, "recipe_version": version},
            )
        return item

    def normalize_inputs(
        self,
        definition: RecipeDefinition,
        raw_inputs: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        unknown = sorted(set(raw_inputs) - set(definition.parameters))
        if unknown:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                "Recipe contains undeclared parameters.",
                422,
                details={"unknown_parameters": unknown},
            )
        result: dict[str, Any] = {}
        actions: list[dict[str, Any]] = []
        for name, parameter in definition.parameters.items():
            if name not in raw_inputs:
                if parameter.required:
                    raise APIError(
                        ErrorCode.RECIPE_PARAMETER_INVALID,
                        f"Recipe parameter is required: {name}",
                        422,
                    )
                if parameter.default is not None:
                    result[name] = parameter.default
                continue
            value = raw_inputs[name]
            converted = self._convert_value(name, value, parameter)
            if converted != value:
                actions.append(
                    {
                        "code": "RECIPE_PARAMETER_COERCED",
                        "path": f"inputs.{name}",
                        "source_field": str(value),
                        "target_field": str(converted),
                    }
                )
            result[name] = converted
        self._validate_cross_parameters(definition.recipe_id, result)
        return result, actions

    @staticmethod
    def _convert_value(
        name: str,
        value: Any,
        parameter: RecipeParameter,
    ) -> Any:
        try:
            if parameter.type == "number":
                if isinstance(value, str):
                    value = float(value)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError
            elif parameter.type == "integer":
                if isinstance(value, str):
                    value = int(value)
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError
            elif parameter.type == "boolean":
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered not in {"true", "false"}:
                        raise ValueError
                    value = lowered == "true"
                if not isinstance(value, bool):
                    raise ValueError
            elif parameter.type == "columns":
                if isinstance(value, str):
                    value = [value]
                if not isinstance(value, list) or not all(
                    isinstance(item, str) and item for item in value
                ):
                    raise ValueError
            elif parameter.type in {"column", "string", "enum"}:
                if not isinstance(value, str) or not value:
                    raise ValueError
        except (TypeError, ValueError) as exc:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                f"Invalid value for recipe parameter: {name}",
                422,
            ) from exc
        if parameter.choices and value not in parameter.choices:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                f"Unsupported value for recipe parameter: {name}",
                422,
                details={"allowed": parameter.choices},
            )
        below_minimum = (
            parameter.minimum is not None
            and (
                value <= parameter.minimum
                if parameter.type == "number" and parameter.minimum == 0
                else value < parameter.minimum
            )
        )
        if below_minimum:
            message = (
                f"Recipe parameter must be greater than zero: {name}"
                if parameter.minimum == 0
                else f"Recipe parameter is below its minimum: {name}"
            )
            raise APIError(ErrorCode.RECIPE_PARAMETER_INVALID, message, 422)
        if parameter.maximum is not None and value > parameter.maximum:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                f"Recipe parameter exceeds its maximum: {name}",
                422,
            )
        return value

    @staticmethod
    def _validate_cross_parameters(recipe_id: str, inputs: dict[str, Any]) -> None:
        if recipe_id == "histogram":
            specified = [name for name in ("bin_width", "bin_count") if name in inputs]
            if len(specified) != 1:
                raise APIError(
                    ErrorCode.RECIPE_PARAMETER_INVALID,
                    "Exactly one of bin_width or bin_count is required.",
                    422,
                )
            if (
                inputs.get("start") is not None
                and inputs.get("end") is not None
                and inputs["start"] >= inputs["end"]
            ):
                raise APIError(
                    ErrorCode.RECIPE_PARAMETER_INVALID,
                    "Histogram start must be less than end.",
                    422,
                )
