from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import polars as pl

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.schemas.analysis import AnalysisPlan, FilterCondition


class DatasetQueryService:
    """Execute the validated, declarative AnalysisPlan with Polars lazy queries."""

    def validate_plan(
        self,
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> tuple[AnalysisPlan, list[str]]:
        if not plan.sources:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Dataset sources are required for preprocessed analysis.",
                400,
            )
        if len(plan.sources) > settings.analysis_join_max_sources:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Analysis plan contains too many dataset sources.",
                413,
            )
        source_columns: dict[str, set[str]] = {}
        source_types: dict[str, str] = {}
        warnings: list[str] = []
        for source in plan.sources:
            manifest = manifests.get(str(source.file_id))
            if manifest is None:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Analysis plan references a file that was not provided.",
                    400,
                    details={"file_id": str(source.file_id)},
                )
            dataset = self._select_dataset(manifest, source.sheet)
            source_columns[source.alias] = {
                str(column["name"]) for column in dataset.get("columns", [])
            }
            source_types.update(
                {
                    f"{source.alias}.{column['name']}": str(column.get("inferred_type", "unknown"))
                    for column in dataset.get("columns", [])
                }
            )
            warnings.extend(dataset.get("warnings", []))

        joined_aliases = {plan.sources[0].alias}
        for join in plan.joins:
            if join.left_alias not in joined_aliases:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Joins must be ordered from an already joined left source.",
                    400,
                )
            if join.right_alias in joined_aliases:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Each dataset source can be joined only once.",
                    400,
                )
            left_columns = [
                self._resolve_reference(
                    column,
                    source_columns,
                    preferred_alias=join.left_alias,
                )
                for column in join.left_on
            ]
            right_columns = [
                self._resolve_reference(
                    column,
                    source_columns,
                    preferred_alias=join.right_alias,
                )
                for column in join.right_on
            ]
            for left, right in zip(left_columns, right_columns, strict=True):
                left_type = source_types.get(left, "unknown")
                right_type = source_types.get(right, "unknown")
                compatible = (
                    left_type == right_type
                    or "unknown" in {left_type, right_type}
                    or {left_type, right_type}.issubset({"integer", "number"})
                )
                if not compatible:
                    raise APIError(
                        ErrorCode.INVALID_REQUEST,
                        "Join key types are incompatible.",
                        400,
                        details={
                            "left": left,
                            "left_type": left_type,
                            "right": right,
                            "right_type": right_type,
                        },
                    )
            joined_aliases.add(join.right_alias)
        disconnected_aliases = sorted(set(source_columns) - joined_aliases)
        if disconnected_aliases:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Every dataset source must be connected by an ordered join.",
                400,
                details={"disconnected_aliases": disconnected_aliases},
            )
        available = {
            f"{alias}.{column}" for alias, columns in source_columns.items() for column in columns
        }
        derived = {item.alias for item in plan.date_buckets}
        source_references = set(plan.select) | set(plan.group_by)
        source_references.update(item.column for item in plan.filters)
        source_references.update(
            item.column for item in plan.aggregations if item.column is not None
        )
        source_references.update(
            item.condition.column for item in plan.aggregations if item.condition is not None
        )
        source_references.update(item.column for item in plan.date_buckets)
        if plan.pivot is not None:
            source_references.update(plan.pivot.index)
            source_references.update([plan.pivot.columns, plan.pivot.values])
        if plan.correlation is not None:
            source_references.update(plan.correlation.columns)
        for reference in source_references:
            if reference in derived:
                continue
            self._resolve_output_reference(reference, available)
        numeric_functions = {"sum", "mean", "std", "percentile"}
        for aggregation in plan.aggregations:
            if aggregation.function in numeric_functions and aggregation.column is not None:
                resolved = self._resolve_output_reference(
                    aggregation.column,
                    available,
                )
                if source_types.get(resolved) not in {
                    "integer",
                    "number",
                    "unknown",
                }:
                    raise APIError(
                        ErrorCode.INVALID_REQUEST,
                        f"Column {aggregation.column} is not numeric.",
                        400,
                    )
        if plan.pivot is not None and plan.pivot.aggregation in {"sum", "mean"}:
            resolved = self._resolve_output_reference(plan.pivot.values, available)
            if source_types.get(resolved) not in {"integer", "number", "unknown"}:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    f"Column {plan.pivot.values} is not numeric.",
                    400,
                )
        if plan.correlation is not None:
            invalid = [
                column
                for column in plan.correlation.columns
                if source_types.get(self._resolve_output_reference(column, available))
                not in {"integer", "number", "unknown"}
            ]
            if invalid:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Correlation columns must be numeric.",
                    400,
                    details={"invalid_columns": invalid},
                )
        if plan.correlation is not None:
            output_columns = {"column", *plan.correlation.columns}
        elif plan.pivot is not None:
            output_columns = set(plan.pivot.index)
            if plan.sort or plan.charts:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Pivot output has dynamic columns; sort or chart it in a follow-up plan.",
                    400,
                )
        elif plan.aggregations:
            output_columns = set(plan.group_by) | {item.alias for item in plan.aggregations}
        else:
            output_columns = set(plan.select)
        requested_output = {item.column for item in plan.sort}
        requested_output.update(item.x_field for item in plan.charts)
        requested_output.update(item.y_field for item in plan.charts)
        requested_output.update(
            item.series_field for item in plan.charts if item.series_field is not None
        )
        requested_output.update(field for item in plan.charts for field in item.tooltip_fields)
        missing_output = sorted(requested_output - output_columns)
        if missing_output:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Sort or chart fields are not present in the analysis output.",
                400,
                details={"missing_output_fields": missing_output},
            )
        return plan, list(dict.fromkeys(warnings))

    def execute(
        self,
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        normalized, warnings = self.validate_plan(plan, manifests)
        frames: dict[str, pl.LazyFrame] = {}
        source_columns: dict[str, set[str]] = {}
        for source in normalized.sources:
            manifest = manifests[str(source.file_id)]
            dataset = self._select_dataset(manifest, source.sheet)
            path = dataset["path"]
            frame = pl.scan_parquet(path)
            columns = set(frame.collect_schema().names())
            source_columns[source.alias] = columns
            frames[source.alias] = frame.rename(
                {column: f"{source.alias}.{column}" for column in columns}
            )

        frame = frames[normalized.sources[0].alias]
        joined_aliases = {normalized.sources[0].alias}
        for join in normalized.joins:
            if join.left_alias not in joined_aliases:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Joins must be ordered from an already joined left source.",
                    400,
                )
            right = frames[join.right_alias]
            left_on = [
                self._qualified(column, join.left_alias, source_columns) for column in join.left_on
            ]
            right_on = [
                self._qualified(column, join.right_alias, source_columns)
                for column in join.right_on
            ]
            frame = frame.join(
                right,
                left_on=left_on,
                right_on=right_on,
                how=join.how,
                coalesce=False,
            )
            joined_aliases.add(join.right_alias)

        available = set(frame.collect_schema().names())
        for bucket in normalized.date_buckets:
            column = self._resolve_output_reference(bucket.column, available)
            expression = pl.col(column)
            dtype = frame.collect_schema()[column]
            if not dtype.is_temporal():
                expression = expression.cast(pl.String).str.to_datetime(strict=False)
            interval = {
                "day": "1d",
                "week": "1w",
                "month": "1mo",
                "quarter": "1q",
                "year": "1y",
            }[bucket.unit]
            frame = frame.with_columns(expression.dt.truncate(interval).alias(bucket.alias))
            available.add(bucket.alias)

        processed_rows = int(frame.select(pl.len()).collect().item())
        if processed_rows > settings.analysis_max_rows:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Preprocessed analysis exceeds the configured row limit.",
                413,
                details={"analysis_max_rows": settings.analysis_max_rows},
            )
        for condition in normalized.filters:
            frame = frame.filter(self._filter_expression(condition, available))
        matched_rows = int(frame.select(pl.len()).collect().item())

        if normalized.correlation is not None:
            rows, columns = self._correlation(frame, normalized, available)
        elif normalized.pivot is not None:
            rows, columns = self._pivot(frame, normalized, available)
        elif normalized.aggregations:
            rows, columns = self._aggregate(frame, normalized, available)
        else:
            selected = [
                self._resolve_output_reference(column, available) for column in normalized.select
            ]
            selected_frame = frame.select(selected)
            total_rows = matched_rows
            rows = (
                selected_frame.head(min(normalized.limit, settings.analysis_result_max_rows))
                .collect()
                .to_dicts()
            )
            columns = normalized.select
            rows = [
                {
                    requested: self._json_value(row.get(actual))
                    for requested, actual in zip(normalized.select, selected, strict=True)
                }
                for row in rows
            ]
            return self._result(
                normalized,
                warnings,
                processed_rows,
                matched_rows,
                total_rows,
                columns,
                rows,
            )

        total_rows = len(rows)
        for sort in reversed(normalized.sort):
            rows.sort(
                key=lambda row, field=sort.column: self._sort_key(row.get(field)),
                reverse=sort.direction == "desc",
            )
        rows = rows[: min(normalized.limit, settings.analysis_result_max_rows)]
        return self._result(
            normalized,
            warnings,
            processed_rows,
            matched_rows,
            total_rows,
            columns,
            rows,
        )

    def _aggregate(
        self,
        frame: pl.LazyFrame,
        plan: AnalysisPlan,
        available: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        group_actual = [
            self._resolve_output_reference(column, available) for column in plan.group_by
        ]
        expressions: list[pl.Expr] = []
        for aggregation in plan.aggregations:
            column = (
                self._resolve_output_reference(aggregation.column, available)
                if aggregation.column is not None
                else None
            )
            if aggregation.function == "count":
                expression = pl.len()
            elif aggregation.function == "distinct_count":
                expression = pl.col(column).n_unique()
            elif aggregation.function == "sum":
                expression = pl.col(column).sum()
            elif aggregation.function == "mean":
                expression = pl.col(column).mean()
            elif aggregation.function == "std":
                expression = pl.col(column).std(ddof=1)
            elif aggregation.function == "min":
                expression = pl.col(column).min()
            elif aggregation.function == "max":
                expression = pl.col(column).max()
            elif aggregation.function == "percentile":
                expression = pl.col(column).quantile(
                    aggregation.percentile,
                    interpolation="linear",
                )
            elif aggregation.function == "count_if":
                expression = (
                    self._filter_expression(
                        aggregation.condition,
                        available,
                    )
                    .cast(pl.Int64)
                    .sum()
                )
            else:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    f"Unsupported aggregation: {aggregation.function}",
                    400,
                )
            expressions.append(expression.alias(aggregation.alias))
        result = (
            frame.group_by(group_actual).agg(expressions)
            if group_actual
            else frame.select(expressions)
        )
        dataframe = result.collect()
        if dataframe.height > settings.analysis_max_groups:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Analysis produced too many groups.",
                413,
                details={"analysis_max_groups": settings.analysis_max_groups},
            )
        rename = {
            actual: requested
            for actual, requested in zip(group_actual, plan.group_by, strict=True)
            if actual != requested
        }
        if rename:
            dataframe = dataframe.rename(rename)
        return (
            [self._json_row(row) for row in dataframe.to_dicts()],
            plan.group_by + [item.alias for item in plan.aggregations],
        )

    def _pivot(
        self,
        frame: pl.LazyFrame,
        plan: AnalysisPlan,
        available: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        pivot = plan.pivot
        index = [self._resolve_output_reference(item, available) for item in pivot.index]
        on = self._resolve_output_reference(pivot.columns, available)
        values = self._resolve_output_reference(pivot.values, available)
        input_rows = int(frame.select(pl.len()).collect().item())
        if input_rows > settings.analysis_advanced_collect_max_rows:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Pivot input exceeds the configured in-memory row limit.",
                413,
                details={
                    "analysis_advanced_collect_max_rows": (
                        settings.analysis_advanced_collect_max_rows
                    )
                },
            )
        dataframe = frame.collect()
        result = dataframe.pivot(
            values=values,
            index=index,
            on=on,
            aggregate_function=pivot.aggregation,
        )
        if result.height > settings.analysis_max_groups:
            raise APIError(ErrorCode.INVALID_REQUEST, "Pivot produced too many rows.", 413)
        rename = {
            actual: requested
            for actual, requested in zip(index, pivot.index, strict=True)
            if actual != requested
        }
        if rename:
            result = result.rename(rename)
        return [self._json_row(row) for row in result.to_dicts()], result.columns

    def _correlation(
        self,
        frame: pl.LazyFrame,
        plan: AnalysisPlan,
        available: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        correlation = plan.correlation
        actual = [
            self._resolve_output_reference(column, available) for column in correlation.columns
        ]
        input_rows = int(frame.select(pl.len()).collect().item())
        if input_rows > settings.analysis_advanced_collect_max_rows:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Correlation input exceeds the configured in-memory row limit.",
                413,
                details={
                    "analysis_advanced_collect_max_rows": (
                        settings.analysis_advanced_collect_max_rows
                    )
                },
            )
        dataframe = frame.select(
            [pl.col(column).cast(pl.Float64, strict=False) for column in actual]
        ).collect()
        rows: list[dict[str, Any]] = []
        for row_name, row_column in zip(
            correlation.columns,
            actual,
            strict=True,
        ):
            row: dict[str, Any] = {"column": row_name}
            for target_name, target_column in zip(
                correlation.columns,
                actual,
                strict=True,
            ):
                value = dataframe.select(
                    pl.corr(
                        row_column,
                        target_column,
                        method=correlation.method,
                    )
                ).item()
                row[target_name] = self._json_value(value)
            rows.append(row)
        return rows, ["column", *correlation.columns]

    def _filter_expression(
        self,
        condition: FilterCondition,
        available: set[str],
    ) -> pl.Expr:
        column = self._resolve_output_reference(condition.column, available)
        expression = pl.col(column)
        target = condition.value
        if condition.operator == "is_null":
            return expression.is_null()
        if condition.operator == "not_null":
            return expression.is_not_null()
        if condition.operator == "contains":
            return expression.cast(pl.String).str.contains(
                str(target),
                literal=True,
            )
        if condition.operator == "in":
            values = target if isinstance(target, list) else [target]
            return expression.is_in(values)
        return {
            "eq": expression == target,
            "ne": expression != target,
            "gt": expression > target,
            "gte": expression >= target,
            "lt": expression < target,
            "lte": expression <= target,
        }[condition.operator]

    def _result(
        self,
        plan: AnalysisPlan,
        warnings: list[str],
        processed_rows: int,
        matched_rows: int,
        total_rows: int,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        charts = [self._chart_payload(chart, rows) for chart in plan.charts]
        return {
            "summary": {
                "processed_rows": processed_rows,
                "matched_rows": matched_rows,
                "result_rows": total_rows,
                "returned_rows": len(rows),
                "truncated": total_rows > len(rows),
                "warnings": warnings,
                "query_engine": "polars",
            },
            "table": {
                "columns": [{"key": name, "label": name} for name in columns],
                "rows": rows,
            },
            "charts": charts,
            "plan": plan.model_dump(mode="json"),
        }

    def _chart_payload(self, chart, rows: list[dict[str, Any]]) -> dict[str, Any]:
        fields = list(
            dict.fromkeys(
                [
                    chart.x_field,
                    chart.y_field,
                    chart.series_field,
                    *chart.tooltip_fields,
                ]
            )
        )
        fields = [field for field in fields if field is not None]
        data = [
            {field: self._json_value(row.get(field)) for field in fields}
            for row in rows[: settings.analysis_chart_max_points]
        ]
        return {
            "schema_version": "2.0",
            "chart_type": chart.type,
            "type": chart.type,
            "title": chart.title,
            "x_field": chart.x_field,
            "y_field": chart.y_field,
            "series_field": chart.series_field,
            "tooltip_fields": chart.tooltip_fields,
            "x_type": chart.x_type,
            "format": {
                "y_unit": chart.y_unit,
                "decimal_places": chart.decimal_places,
            },
            "interaction": {
                "tooltip": True,
                "zoom": chart.zoom,
                "export": True,
            },
            "data": data,
            "truncated": len(rows) > len(data),
        }

    @staticmethod
    def _select_dataset(
        manifest: dict[str, Any],
        sheet: str | None,
    ) -> dict[str, Any]:
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
            ErrorCode.INVALID_REQUEST,
            "Dataset sheet was not found.",
            404,
            details={"available_sheets": [item.get("sheet") for item in datasets]},
        )

    @staticmethod
    def _resolve_reference(
        reference: str,
        source_columns: dict[str, set[str]],
        *,
        preferred_alias: str,
    ) -> str:
        if "." in reference:
            alias, column = reference.split(".", 1)
            if alias != preferred_alias or column not in source_columns.get(alias, set()):
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    f"Unknown dataset column: {reference}",
                    400,
                )
            return reference
        if reference not in source_columns.get(preferred_alias, set()):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                f"Unknown dataset column: {preferred_alias}.{reference}",
                400,
            )
        return f"{preferred_alias}.{reference}"

    @staticmethod
    def _qualified(
        reference: str,
        alias: str,
        source_columns: dict[str, set[str]],
    ) -> str:
        return DatasetQueryService._resolve_reference(
            reference,
            source_columns,
            preferred_alias=alias,
        )

    @staticmethod
    def _resolve_output_reference(reference: str, available: set[str]) -> str:
        if reference in available:
            return reference
        matches = [column for column in available if column.rsplit(".", 1)[-1] == reference]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                f"Ambiguous column reference: {reference}. Use source_alias.column.",
                400,
            )
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            f"Unknown analysis column: {reference}",
            400,
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return None if math.isnan(value) else value
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    def _json_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {key: self._json_value(value) for key, value in row.items()}

    @staticmethod
    def _sort_key(value: Any) -> tuple[int, Any]:
        if value is None:
            return 2, ""
        if isinstance(value, (int, float)):
            return 0, value
        return 1, str(value)


def execute_dataset_analysis(
    *,
    plan_payload: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return DatasetQueryService().execute(
        AnalysisPlan.model_validate(plan_payload),
        manifests,
    )
