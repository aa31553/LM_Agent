from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import mean, median, stdev
from typing import Any, Callable

import polars as pl

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.schemas.analysis import AnalysisPlan, FilterCondition
from app.services.analysis_chart_builder import AnalysisChartBuilder
from app.services.analysis_result_validator import AnalysisResultValidator


class AnalysisRecipeExecutor:
    """Execute compiled recipes without accepting user Python, SQL, or paths."""

    def execute(
        self,
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not plan.recipe_id or not plan.recipe_version:
            raise APIError(
                ErrorCode.RECIPE_NOT_FOUND,
                "Compiled recipe metadata is missing.",
                500,
            )
        frames = self._load_frames(plan, manifests)
        if plan.recipe_id == "join_compare":
            processed_rows = sum(frame.height for frame in frames.values())
            rows, warnings, summary_extra = self._join_compare(plan, frames)
            matched_rows = processed_rows
        else:
            frame = frames[plan.sources[0].alias]
            processed_rows = frame.height
            frame = self._apply_filters(frame, plan.filters)
            matched_rows = frame.height
            handler = self._handler(plan.recipe_id)
            rows, warnings, summary_extra = handler(plan, frame)
        total_rows = len(rows)
        returned = rows[: settings.analysis_result_max_rows]
        if total_rows > len(returned):
            warnings.append("Result rows were truncated to the configured limit.")
        AnalysisResultValidator().validate(
            returned,
            plan.result_contract,
            recipe_id=plan.recipe_id,
        )
        charts, chart_warnings = AnalysisChartBuilder().build(
            plan.recipe_id,
            returned,
            title=plan.recipe_title,
            enabled=plan.chart_enabled,
        )
        warnings.extend(chart_warnings)
        warnings = list(dict.fromkeys(warnings))
        result_hash = hashlib.sha256(
            json.dumps(returned, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": "3.0",
            "summary": {
                "processed_rows": processed_rows,
                "matched_rows": matched_rows,
                "result_rows": total_rows,
                "returned_rows": len(returned),
                "truncated": total_rows > len(returned),
                "warnings": warnings,
                "query_engine": "polars",
                "recipe_id": plan.recipe_id,
                "recipe_version": plan.recipe_version,
                **summary_extra,
            },
            "schema": {
                "columns": self._schema(returned, plan.result_contract),
            },
            "table": {
                "columns": [
                    {"key": name, "label": name}
                    for name in (list(returned[0]) if returned else plan.result_contract)
                ],
                "rows": returned,
            },
            "charts": charts,
            "lineage": {
                "dataset_hashes": self._dataset_hashes(plan, manifests),
                "compiler_version": plan.compiler_version,
                "result_hash": result_hash,
            },
            "plan": plan.model_dump(mode="json"),
        }

    def _handler(
        self,
        recipe_id: str,
    ) -> Callable[
        [AnalysisPlan, pl.DataFrame],
        tuple[list[dict[str, Any]], list[str], dict[str, Any]],
    ]:
        handlers = {
            "histogram": self._histogram,
            "category_summary": self._category_summary,
            "trend_summary": self._trend_summary,
            "pareto": self._pareto,
            "data_quality_summary": self._data_quality_summary,
            "group_summary": self._group_summary,
            "missing_value_summary": self._missing_value_summary,
            "duplicate_summary": self._duplicate_summary,
            "type_cast": self._type_cast,
            "date_parts": self._date_parts,
            "derive_arithmetic": self._derive_arithmetic,
            "pivot_table": self._pivot_table,
        }
        handler = handlers.get(recipe_id)
        if handler is None:
            raise APIError(
                ErrorCode.RECIPE_NOT_FOUND,
                f"Recipe executor is not implemented: {recipe_id}",
                500,
            )
        return handler

    def _load_frames(
        self,
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> dict[str, pl.DataFrame]:
        frames: dict[str, pl.DataFrame] = {}
        for source in plan.sources:
            manifest = manifests.get(str(source.file_id))
            if manifest is None:
                raise APIError(
                    ErrorCode.PERMISSION_DENIED,
                    "Compiled recipe references an unavailable dataset.",
                    403,
                )
            dataset = self._select_dataset(manifest, source.sheet)
            lazy = pl.scan_parquet(dataset["path"])
            row_count = int(lazy.select(pl.len()).collect().item())
            if row_count > settings.analysis_max_rows:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Recipe source exceeds the configured row limit.",
                    413,
                )
            if row_count > settings.analysis_advanced_collect_max_rows:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "Recipe source exceeds the configured in-memory execution limit.",
                    413,
                    details={
                        "analysis_advanced_collect_max_rows": (
                            settings.analysis_advanced_collect_max_rows
                        )
                    },
                )
            names = lazy.collect_schema().names()
            frames[source.alias] = lazy.rename(
                {name: f"{source.alias}.{name}" for name in names}
            ).collect()
        return frames

    def _apply_filters(
        self,
        frame: pl.DataFrame,
        filters: list[FilterCondition],
    ) -> pl.DataFrame:
        available = set(frame.columns)
        for condition in filters:
            column = self._resolve(condition.column, available)
            expression = pl.col(column)
            if condition.operator == "is_null":
                predicate = expression.is_null()
            elif condition.operator == "not_null":
                predicate = expression.is_not_null()
            elif condition.operator == "contains":
                predicate = expression.cast(pl.String).str.contains(
                    str(condition.value),
                    literal=True,
                )
            elif condition.operator == "in":
                values = (
                    condition.value
                    if isinstance(condition.value, list)
                    else [condition.value]
                )
                predicate = expression.is_in(values)
            else:
                predicate = {
                    "eq": expression == condition.value,
                    "ne": expression != condition.value,
                    "gt": expression > condition.value,
                    "gte": expression >= condition.value,
                    "lt": expression < condition.value,
                    "lte": expression <= condition.value,
                }[condition.operator]
            frame = frame.filter(predicate)
        return frame

    def _histogram(
        self,
        plan: AnalysisPlan,
        frame: pl.DataFrame,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        inputs = plan.recipe_inputs
        column = self._resolve(inputs["source_field"], set(frame.columns))
        raw_values = frame[column].cast(pl.Float64, strict=False).to_list()
        values = [
            float(value)
            for value in raw_values
            if value is not None and math.isfinite(float(value))
        ]
        null_count = len(raw_values) - len(values)
        if not values:
            return [], ["Histogram source contains no numeric values."], {
                "null_count": null_count,
                "underflow_count": 0,
                "overflow_count": 0,
            }
        minimum = min(values)
        maximum = max(values)
        if "bin_width" in inputs:
            width = float(inputs["bin_width"])
            start = float(inputs.get("start", math.floor(minimum / width) * width))
            if "end" in inputs:
                end = float(inputs["end"])
            else:
                end = math.ceil(maximum / width) * width
                if math.isclose(end, start):
                    end = start + width
            bin_count = max(1, int(math.ceil((end - start) / width)))
            end = start + bin_count * width
        else:
            bin_count = int(inputs["bin_count"])
            start = float(inputs.get("start", minimum))
            end = float(inputs.get("end", maximum))
            if math.isclose(start, end):
                end = start + 1.0
            width = (end - start) / bin_count
        if bin_count > 200:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                "Histogram would exceed the 200-bin limit.",
                422,
                details={"bin_count": bin_count},
            )
        counts = [0] * bin_count
        underflow = 0
        overflow = 0
        for value in values:
            if value < start:
                underflow += 1
                if inputs.get("include_underflow"):
                    counts[0] += 1
                continue
            if value > end:
                overflow += 1
                if inputs.get("include_overflow"):
                    counts[-1] += 1
                continue
            if math.isclose(value, end):
                index = bin_count - 1
            else:
                index = int(math.floor((value - start) / width))
            if index < 0:
                underflow += 1
            elif index >= bin_count:
                overflow += 1
            else:
                counts[index] += 1
        rows = []
        for index, count in enumerate(counts):
            bin_start = start + index * width
            bin_end = bin_start + width
            closing = "]" if index == bin_count - 1 else ")"
            rows.append(
                {
                    "bin_start": self._clean_number(bin_start),
                    "bin_end": self._clean_number(bin_end),
                    "bin_label": (
                        f"[{self._format_number(bin_start)}, "
                        f"{self._format_number(bin_end)}{closing}"
                    ),
                    "record_count": count,
                }
            )
        return rows, [], {
            "null_count": null_count if inputs.get("include_null_count", True) else None,
            "underflow_count": underflow,
            "overflow_count": overflow,
        }

    def _category_summary(self, plan, frame):
        inputs = plan.recipe_inputs
        category = self._resolve(inputs["category_field"], set(frame.columns))
        value = self._optional_column(inputs.get("value_field"), frame)
        grouped = self._group_values(frame, [category], value, inputs["aggregation"])
        rows = [
            {"category": self._json_value(keys[0]), "value": result}
            for keys, result in grouped
        ]
        return self._bounded(rows, sort_key=lambda row: str(row["category"]))

    def _trend_summary(self, plan, frame):
        inputs = plan.recipe_inputs
        date_column = self._resolve(inputs["date_field"], set(frame.columns))
        value_column = self._optional_column(inputs.get("value_field"), frame)
        series_column = self._optional_column(inputs.get("series_field"), frame)
        buckets: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
        for row in frame.select(
            [item for item in (date_column, value_column, series_column) if item]
        ).iter_rows(named=True):
            parsed = self._parse_datetime(row.get(date_column))
            if parsed is None:
                continue
            period = self._period(parsed, inputs["period"])
            series = row.get(series_column) if series_column else None
            buckets[(period, series)].append(row.get(value_column) if value_column else 1)
        rows = []
        for (period, series), values in sorted(
            buckets.items(),
            key=lambda item: (item[0][0], str(item[0][1])),
        ):
            result = self._aggregate_values(values, inputs["aggregation"])
            output = {"period": period, "value": result}
            if series_column:
                output["series"] = self._json_value(series)
            rows.append(output)
        return self._bounded(rows)

    def _pareto(self, plan, frame):
        column = self._resolve(plan.recipe_inputs["category_field"], set(frame.columns))
        counts = Counter(self._json_value(value) for value in frame[column].to_list())
        total = sum(counts.values())
        cumulative = 0
        rows = []
        for category, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], str(item[0])),
        ):
            cumulative += count
            rows.append(
                {
                    "category": category,
                    "count": count,
                    "ratio": count / total if total else 0,
                    "cumulative_ratio": cumulative / total if total else 0,
                }
            )
        return self._bounded(rows)

    def _data_quality_summary(self, plan, frame):
        fields = plan.recipe_inputs.get("fields") or list(frame.columns)
        rows = []
        for reference in fields:
            column = self._resolve(reference, set(frame.columns))
            series = frame[column]
            rows.append(
                {
                    "column": reference,
                    "inferred_type": self._inferred_type(series.dtype),
                    "null_count": series.null_count(),
                    "null_ratio": series.null_count() / frame.height if frame.height else 0,
                    "distinct_count": series.n_unique(),
                }
            )
        return rows, [], {}

    def _group_summary(self, plan, frame):
        inputs = plan.recipe_inputs
        groups = [
            self._resolve(reference, set(frame.columns))
            for reference in inputs["group_fields"]
        ]
        value = self._optional_column(inputs.get("value_field"), frame)
        grouped = self._group_values(frame, groups, value, inputs["aggregation"])
        rows = [
            {
                "group": " | ".join(str(self._json_value(item)) for item in keys),
                "value": result,
            }
            for keys, result in grouped
        ]
        return self._bounded(rows, sort_key=lambda row: row["group"])

    def _missing_value_summary(self, plan, frame):
        fields = plan.recipe_inputs.get("fields") or list(frame.columns)
        rows = []
        for reference in fields:
            column = self._resolve(reference, set(frame.columns))
            null_count = frame[column].null_count()
            rows.append(
                {
                    "column": reference,
                    "null_count": null_count,
                    "null_ratio": null_count / frame.height if frame.height else 0,
                }
            )
        return rows, [], {}

    def _duplicate_summary(self, plan, frame):
        columns = [
            self._resolve(reference, set(frame.columns))
            for reference in plan.recipe_inputs["key_fields"]
        ]
        counts = Counter(frame.select(columns).iter_rows())
        rows = [
            {
                "key": " | ".join(str(self._json_value(item)) for item in key),
                "duplicate_count": count,
            }
            for key, count in counts.items()
            if count > 1
        ]
        rows.sort(key=lambda row: (-row["duplicate_count"], row["key"]))
        return self._bounded(rows)

    def _type_cast(self, plan, frame):
        inputs = plan.recipe_inputs
        column = self._resolve(inputs["source_field"], set(frame.columns))
        rows = []
        failures = 0
        for source in frame[column].to_list():
            cast_value = self._cast_value(source, inputs["target_type"])
            failed = source is not None and cast_value is None
            failures += int(failed)
            rows.append(
                {
                    "source_value": self._json_value(source),
                    "cast_value": self._json_value(cast_value),
                    "cast_failed": failed,
                }
            )
        return rows, [], {"parse_failure_count": failures}

    def _date_parts(self, plan, frame):
        column = self._resolve(plan.recipe_inputs["source_field"], set(frame.columns))
        rows = []
        failures = 0
        for source in frame[column].to_list():
            value = self._parse_datetime(source)
            if value is None:
                failures += int(source is not None)
                rows.append(
                    {
                        "source_value": self._json_value(source),
                        "year": None,
                        "quarter": None,
                        "month": None,
                        "week": None,
                        "day": None,
                    }
                )
                continue
            rows.append(
                {
                    "source_value": self._json_value(source),
                    "year": value.year,
                    "quarter": (value.month - 1) // 3 + 1,
                    "month": value.month,
                    "week": value.isocalendar().week,
                    "day": value.day,
                }
            )
        return rows, [], {"parse_failure_count": failures}

    def _derive_arithmetic(self, plan, frame):
        inputs = plan.recipe_inputs
        left = self._resolve(inputs["left_field"], set(frame.columns))
        right = self._resolve(inputs["right_field"], set(frame.columns))
        operation = inputs["operation"]
        rows = []
        divide_by_zero = 0
        for left_value, right_value in frame.select([left, right]).iter_rows():
            try:
                a = float(left_value)
                b = float(right_value)
                if operation == "add":
                    result = a + b
                elif operation == "subtract":
                    result = a - b
                elif operation == "multiply":
                    result = a * b
                elif operation in {"divide", "ratio"}:
                    if b == 0:
                        divide_by_zero += 1
                        result = None
                    else:
                        result = a / b
                else:
                    result = None
            except (TypeError, ValueError):
                result = None
            rows.append({"derived_value": self._clean_number(result)})
        warnings = (
            ["Division by zero produced null derived values."] if divide_by_zero else []
        )
        return rows, warnings, {"division_by_zero_count": divide_by_zero}

    def _pivot_table(self, plan, frame):
        inputs = plan.recipe_inputs
        index = [
            self._resolve(reference, set(frame.columns))
            for reference in inputs["index_fields"]
        ]
        on = self._resolve(inputs["column_field"], set(frame.columns))
        values = self._resolve(inputs["value_field"], set(frame.columns))
        result = frame.pivot(
            values=values,
            index=index,
            on=on,
            aggregate_function=inputs["aggregation"],
        )
        if result.height > settings.analysis_max_groups:
            result = result.head(settings.analysis_max_groups)
            warnings = ["Pivot rows were limited by the configured group limit."]
        else:
            warnings = []
        rows = [self._json_row(row) for row in result.to_dicts()]
        return rows, warnings, {}

    def _join_compare(self, plan, frames):
        inputs = plan.recipe_inputs
        aliases = [source.alias for source in plan.sources]
        left_frame = frames[aliases[0]]
        right_frame = frames[aliases[1]]
        left_key = self._resolve(inputs["left_key"], set(left_frame.columns))
        right_key = self._resolve(inputs["right_key"], set(right_frame.columns))
        left = Counter(self._json_value(value) for value in left_frame[left_key].to_list())
        right = Counter(self._json_value(value) for value in right_frame[right_key].to_list())
        rows = []
        for key in sorted(set(left) | set(right), key=str):
            left_count = left.get(key, 0)
            right_count = right.get(key, 0)
            status = (
                "matched"
                if left_count and right_count
                else "left_only"
                if left_count
                else "right_only"
            )
            rows.append(
                {
                    "key": key,
                    "left_count": left_count,
                    "right_count": right_count,
                    "match_status": status,
                }
            )
        return self._bounded(rows)

    def _group_values(
        self,
        frame: pl.DataFrame,
        group_columns: list[str],
        value_column: str | None,
        aggregation: str,
    ) -> list[tuple[tuple[Any, ...], Any]]:
        selected = [*group_columns, *([value_column] if value_column else [])]
        groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
        for row in frame.select(selected).iter_rows(named=True):
            key = tuple(row[column] for column in group_columns)
            groups[key].append(row.get(value_column) if value_column else 1)
        return [
            (key, self._aggregate_values(values, aggregation))
            for key, values in groups.items()
        ]

    @staticmethod
    def _aggregate_values(values: list[Any], aggregation: str) -> Any:
        populated = [value for value in values if value is not None]
        if aggregation == "count":
            return len(values)
        numeric = []
        for value in populated:
            try:
                numeric.append(float(value))
            except (TypeError, ValueError):
                continue
        if not numeric:
            return None
        return {
            "sum": lambda: sum(numeric),
            "mean": lambda: mean(numeric),
            "median": lambda: median(numeric),
            "std": lambda: stdev(numeric) if len(numeric) > 1 else None,
            "min": lambda: min(numeric),
            "max": lambda: max(numeric),
        }[aggregation]()

    def _bounded(
        self,
        rows: list[dict[str, Any]],
        *,
        sort_key=None,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        if sort_key:
            rows.sort(key=sort_key)
        if len(rows) <= settings.analysis_max_groups:
            return rows, [], {}
        return (
            rows[: settings.analysis_max_groups],
            ["High-cardinality result was limited to the configured group limit."],
            {"unbounded_group_count": len(rows)},
        )

    @staticmethod
    def _select_dataset(manifest: dict[str, Any], sheet: str | None) -> dict[str, Any]:
        datasets = manifest.get("datasets", [])
        if sheet is None and datasets:
            return datasets[0]
        for dataset in datasets:
            if dataset.get("sheet") == sheet or dataset.get("key") == sheet:
                return dataset
        raise APIError(ErrorCode.DOCUMENT_NOT_READY, "Recipe dataset is unavailable.", 409)

    @staticmethod
    def _resolve(reference: str, available: set[str]) -> str:
        if reference in available:
            return reference
        matches = [name for name in available if name.rsplit(".", 1)[-1] == reference]
        if len(matches) == 1:
            return matches[0]
        code = ErrorCode.COLUMN_AMBIGUOUS if matches else ErrorCode.COLUMN_NOT_FOUND
        raise APIError(code, f"Recipe column cannot be resolved: {reference}", 422)

    def _optional_column(
        self,
        reference: str | None,
        frame: pl.DataFrame,
    ) -> str | None:
        return self._resolve(reference, set(frame.columns)) if reference else None

    @staticmethod
    def _period(value: datetime, period: str) -> str:
        if period == "day":
            return value.date().isoformat()
        if period == "week":
            year, week, _ = value.isocalendar()
            return f"{year}-W{week:02d}"
        if period == "month":
            return f"{value.year:04d}-{value.month:02d}"
        if period == "quarter":
            return f"{value.year:04d}-Q{(value.month - 1) // 3 + 1}"
        return f"{value.year:04d}"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    @staticmethod
    def _cast_value(value: Any, target_type: str) -> Any:
        if value is None:
            return None
        try:
            if target_type == "string":
                return str(value)
            if target_type == "integer":
                return int(value)
            if target_type == "number":
                return float(value)
            if target_type == "boolean":
                if isinstance(value, bool):
                    return value
                lowered = str(value).strip().lower()
                if lowered in {"true", "1", "yes", "y"}:
                    return True
                if lowered in {"false", "0", "no", "n"}:
                    return False
                return None
            parsed = AnalysisRecipeExecutor._parse_datetime(value)
            if parsed is None:
                return None
            return parsed.date() if target_type == "date" else parsed
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _inferred_type(dtype: pl.DataType) -> str:
        if dtype.is_integer():
            return "integer"
        if dtype.is_float() or dtype.is_decimal():
            return "number"
        if dtype.is_temporal():
            return "datetime"
        if dtype == pl.Boolean:
            return "boolean"
        return "string"

    @staticmethod
    def _schema(
        rows: list[dict[str, Any]],
        contract: list[str],
    ) -> list[dict[str, str]]:
        names = list(rows[0]) if rows else contract
        result = []
        for name in names:
            value = next((row.get(name) for row in rows if row.get(name) is not None), None)
            value_type = (
                "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "number"
                if isinstance(value, float)
                else "string"
            )
            result.append({"key": name, "type": value_type, "semantic_type": name})
        return result

    @staticmethod
    def _dataset_hashes(
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> list[str]:
        hashes = []
        for source in plan.sources:
            manifest = manifests.get(str(source.file_id), {})
            payload = json.dumps(manifest, sort_keys=True, default=str).encode("utf-8")
            hashes.append(hashlib.sha256(payload).hexdigest())
        return hashes

    @staticmethod
    def _clean_number(value: float | None) -> int | float | None:
        if value is None or not math.isfinite(value):
            return None
        rounded = round(value, 12)
        return int(rounded) if rounded.is_integer() else rounded

    @staticmethod
    def _format_number(value: float) -> str:
        clean = AnalysisRecipeExecutor._clean_number(value)
        return str(clean)

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
