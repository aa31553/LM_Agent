from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.schemas.analysis import AnalysisPlan, FilterCondition
from app.schemas.analysis_recipe import IntentDraft
from app.services.analysis_filter_value_service import validate_temporal_filter_value
from app.services.analysis_recipe_registry import AnalysisRecipeRegistry


class AnalysisRecipeCompiler:
    COMPILER_VERSION = "1.0"
    RECIPE_ALIASES = {
        "hist": "histogram",
        "直方圖": "histogram",
        "类别汇总": "category_summary",
        "類別彙總": "category_summary",
        "趨勢": "trend_summary",
        "月份疊圖": "period_overlay",
        "月份對比": "period_overlay",
        "帕累托": "pareto",
    }

    def __init__(self, registry: AnalysisRecipeRegistry | None = None) -> None:
        self.registry = registry or AnalysisRecipeRegistry()

    def compile(
        self,
        intent: IntentDraft,
        manifests: dict[str, dict[str, Any]],
        *,
        allowed_file_ids: set[str] | None = None,
        plan_origin: str = "recipe",
    ) -> tuple[IntentDraft, AnalysisPlan, list[dict[str, Any]], list[str]]:
        if not settings.analysis_recipes_enabled:
            raise APIError(
                ErrorCode.RECIPE_NOT_FOUND,
                "Analysis recipes are disabled by the rollout feature flag.",
                503,
            )
        actions: list[dict[str, Any]] = []
        canonical_recipe_id = self.RECIPE_ALIASES.get(intent.recipe_id, intent.recipe_id)
        if canonical_recipe_id != intent.recipe_id:
            actions.append(
                {
                    "code": "RECIPE_ALIAS_MAPPED",
                    "path": "recipe_id",
                    "source_field": intent.recipe_id,
                    "target_field": canonical_recipe_id,
                }
            )
        definition = self.registry.get(canonical_recipe_id, intent.recipe_version)
        source_columns, source_types = self._source_schema(
            intent,
            manifests,
            allowed_file_ids=allowed_file_ids,
        )
        normalized_inputs, parameter_actions = self.registry.normalize_inputs(
            definition,
            intent.inputs,
        )
        actions.extend(parameter_actions)
        for name, parameter in definition.parameters.items():
            if name not in normalized_inputs:
                continue
            if parameter.type == "column":
                normalized_inputs[name] = self._resolve_column(
                    normalized_inputs[name],
                    source_columns,
                )
            elif parameter.type == "columns":
                normalized_inputs[name] = [
                    self._resolve_column(item, source_columns)
                    for item in normalized_inputs[name]
                ]
        self._validate_types(canonical_recipe_id, normalized_inputs, source_types)
        filters, filter_actions = self._filters(
            intent.filters,
            source_columns,
            source_types,
        )
        actions.extend(filter_actions)
        normalized_intent = intent.model_copy(
            update={
                "recipe_id": canonical_recipe_id,
                "inputs": normalized_inputs,
                "filters": [item.model_dump(mode="json") for item in filters],
            }
        )
        result_contract = list(definition.result_schema)
        if (
            canonical_recipe_id == "trend_summary"
            and normalized_inputs.get("series_field") is not None
        ):
            result_contract.append("series")
        plan = AnalysisPlan(
            sources=intent.sources,
            filters=filters,
            plan_origin=plan_origin,
            recipe_id=canonical_recipe_id,
            recipe_version=definition.version,
            recipe_inputs=normalized_inputs,
            compiler_version=self.COMPILER_VERSION,
            result_contract=result_contract,
            chart_enabled=intent.chart_enabled,
            recipe_title=intent.title,
        )
        warnings: list[str] = []
        if canonical_recipe_id != "join_compare" and len(intent.sources) > 1:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                "This recipe accepts exactly one dataset source.",
                422,
            )
        if canonical_recipe_id == "join_compare" and len(intent.sources) != 2:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                "join_compare requires exactly two dataset sources.",
                422,
            )
        return normalized_intent, plan, actions, warnings

    @staticmethod
    def _source_schema(
        intent: IntentDraft,
        manifests: dict[str, dict[str, Any]],
        *,
        allowed_file_ids: set[str] | None,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        aliases = [source.alias for source in intent.sources]
        if len(aliases) != len(set(aliases)):
            raise APIError(
                ErrorCode.ANALYSIS_INTENT_INVALID,
                "Dataset source aliases must be unique.",
                422,
            )
        source_columns: dict[str, list[dict[str, Any]]] = {}
        source_types: dict[str, str] = {}
        for source in intent.sources:
            file_id = str(source.file_id)
            if allowed_file_ids is not None and file_id not in allowed_file_ids:
                raise APIError(
                    ErrorCode.PERMISSION_DENIED,
                    "Intent references a file outside the approved source set.",
                    403,
                )
            manifest = manifests.get(file_id)
            if manifest is None:
                raise APIError(
                    ErrorCode.PERMISSION_DENIED,
                    "Intent references an unavailable file.",
                    403,
                    details={"file_id": file_id},
                )
            dataset = AnalysisRecipeCompiler._select_dataset(manifest, source.sheet)
            columns = list(dataset.get("columns", []))
            source_columns[source.alias] = columns
            for column in columns:
                source_types[f"{source.alias}.{column['name']}"] = str(
                    column.get("inferred_type", "unknown")
                )
        return source_columns, source_types

    @staticmethod
    def _select_dataset(manifest: dict[str, Any], sheet: str | None) -> dict[str, Any]:
        datasets = manifest.get("datasets", [])
        if not datasets:
            raise APIError(
                ErrorCode.DOCUMENT_NOT_READY,
                "Spreadsheet preprocessing is not complete.",
                409,
            )
        if sheet is None:
            return datasets[0]
        for dataset in datasets:
            if dataset.get("sheet") == sheet or dataset.get("key") == sheet:
                return dataset
        raise APIError(
            ErrorCode.COLUMN_NOT_FOUND,
            "Dataset sheet was not found.",
            404,
            details={"available_sheets": [item.get("sheet") for item in datasets]},
        )

    @staticmethod
    def _resolve_column(
        reference: str,
        source_columns: dict[str, list[dict[str, Any]]],
    ) -> str:
        candidates: list[str] = []
        if "." in reference:
            alias, name = reference.split(".", 1)
            for column in source_columns.get(alias, []):
                if name in {
                    str(column.get("name")),
                    str(column.get("column_id")),
                    str(column.get("display_name")),
                    str(column.get("normalized_name")),
                }:
                    candidates.append(f"{alias}.{column['name']}")
        else:
            for alias, columns in source_columns.items():
                for column in columns:
                    if reference in {
                        str(column.get("name")),
                        str(column.get("column_id")),
                        str(column.get("display_name")),
                        str(column.get("normalized_name")),
                    }:
                        candidates.append(f"{alias}.{column['name']}")
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            raise APIError(
                ErrorCode.COLUMN_NOT_FOUND,
                f"Column was not found: {reference}",
                422,
            )
        if len(candidates) > 1:
            raise APIError(
                ErrorCode.COLUMN_AMBIGUOUS,
                f"Column is ambiguous: {reference}",
                422,
                details={"candidates": candidates},
            )
        return candidates[0]

    @staticmethod
    def _filters(
        raw_filters: list[dict[str, Any]],
        source_columns: dict[str, list[dict[str, Any]]],
        source_types: dict[str, str],
    ) -> tuple[list[FilterCondition], list[dict[str, Any]]]:
        filters: list[FilterCondition] = []
        actions: list[dict[str, Any]] = []
        for index, raw_filter in enumerate(raw_filters):
            candidate = dict(raw_filter)
            if "column" in candidate:
                candidate["column"] = AnalysisRecipeCompiler._resolve_column(
                    str(candidate["column"]),
                    source_columns,
                )
            candidates = AnalysisRecipeCompiler._expand_between_filter(candidate, index)
            normalized_candidates = []
            for expanded in candidates:
                normalized, temporal_action = (
                    AnalysisRecipeCompiler._normalize_temporal_end_filter(
                        expanded,
                        source_types.get(str(expanded.get("column")), "unknown"),
                        index,
                    )
                )
                normalized_candidates.append(normalized)
                if temporal_action is not None:
                    actions.append(temporal_action)
            if candidate.get("operator") == "between":
                actions.append(
                    {
                        "code": "FILTER_BETWEEN_EXPANDED",
                        "path": f"filters[{index}]",
                        "source_field": "between",
                        "target_field": ",".join(
                            str(item["operator"]) for item in normalized_candidates
                        ),
                    }
                )
            for expanded in normalized_candidates:
                try:
                    condition = FilterCondition.model_validate(expanded)
                except ValidationError as exc:
                    raise APIError(
                        ErrorCode.RECIPE_PARAMETER_INVALID,
                        "Intent contains an invalid filter.",
                        422,
                        details={"errors": exc.errors(include_url=False)},
                    ) from exc
                if condition.operator not in {
                    "contains",
                    "is_null",
                    "not_null",
                }:
                    validate_temporal_filter_value(
                        condition.value,
                        source_types.get(condition.column, "unknown"),
                        column=condition.column,
                        operator=condition.operator,
                        path=f"filters[{index}].value",
                    )
                filters.append(condition)
        return filters, actions

    @staticmethod
    def _expand_between_filter(
        candidate: dict[str, Any],
        index: int,
    ) -> list[dict[str, Any]]:
        if candidate.get("operator") != "between":
            return [candidate]
        values = candidate.get("value")
        if (
            not isinstance(values, list)
            or len(values) != 2
            or values[0] is None
            or values[1] is None
        ):
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                "between filter requires exactly two non-null boundary values.",
                422,
                details={
                    "path": f"filters[{index}].value",
                    "operator": "between",
                    "value": values,
                    "repair_hint": (
                        "Provide [start, end], or use separate gte and lte filters."
                    ),
                },
            )
        base = {key: value for key, value in candidate.items() if key != "value"}
        return [
            {**base, "operator": "gte", "value": values[0]},
            {**base, "operator": "lte", "value": values[1]},
        ]

    @staticmethod
    def _normalize_temporal_end_filter(
        candidate: dict[str, Any],
        inferred_type: str,
        index: int,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        value = candidate.get("value")
        if (
            inferred_type != "datetime"
            or candidate.get("operator") != "lte"
            or not isinstance(value, str)
        ):
            return candidate, None
        try:
            inclusive_end = date.fromisoformat(value.strip())
        except ValueError:
            return candidate, None
        exclusive_end = inclusive_end + timedelta(days=1)
        return (
            {
                **candidate,
                "operator": "lt",
                "value": exclusive_end.isoformat(),
            },
            {
                "code": "TEMPORAL_INCLUSIVE_END_NORMALIZED",
                "path": f"filters[{index}]",
                "source_field": value,
                "target_field": exclusive_end.isoformat(),
            },
        )

    @staticmethod
    def _validate_types(
        recipe_id: str,
        inputs: dict[str, Any],
        source_types: dict[str, str],
    ) -> None:
        numeric_parameters = {
            "histogram": ["source_field"],
            "derive_arithmetic": ["left_field", "right_field"],
            "descriptive_statistics": ["source_field"],
            "boxplot_summary": ["source_field"],
            "outlier_iqr": ["source_field"],
            "correlation_matrix": ["fields"],
            "spec_judgement": ["source_field"],
            "process_capability": ["source_field"],
            "control_chart": (
                ["source_field"]
                if inputs.get("chart_type") in {"i_mr", "xbar_r"}
                else []
            ),
        }.get(recipe_id, [])
        if (
            recipe_id
            in {
                "category_summary",
                "trend_summary",
                "period_overlay",
                "group_summary",
            }
            and inputs.get("aggregation") != "count"
            and inputs.get("value_field") is not None
        ):
            numeric_parameters = [*numeric_parameters, "value_field"]
        for name in numeric_parameters:
            references = inputs[name] if isinstance(inputs[name], list) else [inputs[name]]
            for reference in references:
                column_type = source_types.get(reference, "unknown")
                if column_type not in {"integer", "number", "unknown"}:
                    raise APIError(
                        ErrorCode.COLUMN_TYPE_INCOMPATIBLE,
                        f"Recipe requires a numeric column: {reference}",
                        422,
                        details={"inferred_type": column_type},
                    )
