from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import polars as pl

from app.core.constants import ErrorCode
from app.core.exceptions import APIError


def coerce_polars_filter_value(
    value: Any,
    dtype: pl.DataType,
    *,
    column: str,
    operator: str,
    path: str,
) -> Any:
    """Convert JSON temporal values to the native type expected by Polars."""

    base_type = dtype.base_type()
    if base_type not in {pl.Date, pl.Datetime, pl.Time}:
        return value
    if isinstance(value, list):
        return [
            coerce_polars_filter_value(
                item,
                dtype,
                column=column,
                operator=operator,
                path=f"{path}[{index}]",
            )
            for index, item in enumerate(value)
        ]
    try:
        if base_type == pl.Date:
            return _to_date(value)
        if base_type == pl.Datetime:
            return _to_datetime(value, getattr(dtype, "time_zone", None))
        return _to_time(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise _temporal_filter_error(
            value,
            dtype,
            column=column,
            operator=operator,
            path=path,
        ) from exc


def validate_temporal_filter_value(
    value: Any,
    inferred_type: str,
    *,
    column: str,
    operator: str,
    path: str,
) -> None:
    """Reject malformed temporal JSON before a plan is queued for execution."""

    if inferred_type != "datetime":
        return
    values = value if isinstance(value, list) else [value]
    for index, item in enumerate(values):
        item_path = f"{path}[{index}]" if isinstance(value, list) else path
        try:
            _to_datetime(item, None)
        except (TypeError, ValueError) as exc:
            raise APIError(
                ErrorCode.RECIPE_PARAMETER_INVALID,
                "Filter value is not a valid ISO 8601 date or datetime.",
                422,
                details={
                    "path": item_path,
                    "column": column,
                    "operator": operator,
                    "value": item,
                    "inferred_type": inferred_type,
                    "expected_format": "YYYY-MM-DD or ISO 8601 datetime",
                },
            ) from exc


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise TypeError("date filter values must be ISO strings or date objects")
    text = _normalize_iso(value)
    try:
        return date.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(text).date()


def _to_datetime(value: Any, time_zone: str | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(_normalize_iso(value))
    else:
        raise TypeError("datetime filter values must be ISO strings or temporal objects")

    if time_zone:
        zone = ZoneInfo(time_zone)
        return (
            parsed.replace(tzinfo=zone)
            if parsed.tzinfo is None
            else parsed.astimezone(zone)
        )
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _to_time(value: Any) -> time:
    if isinstance(value, datetime):
        parsed = value.timetz()
    elif isinstance(value, time):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = time.fromisoformat(_normalize_iso(value))
    else:
        raise TypeError("time filter values must be ISO strings or time objects")
    if parsed.tzinfo is not None:
        raise ValueError("Polars Time columns do not carry timezone information")
    return parsed


def _normalize_iso(value: str) -> str:
    text = value.strip()
    return f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text


def _temporal_filter_error(
    value: Any,
    dtype: pl.DataType,
    *,
    column: str,
    operator: str,
    path: str,
) -> APIError:
    base_type = dtype.base_type()
    expected = {
        pl.Date: "YYYY-MM-DD",
        pl.Datetime: "YYYY-MM-DD or ISO 8601 datetime",
        pl.Time: "HH:MM[:SS[.ffffff]]",
    }[base_type]
    return APIError(
        ErrorCode.RECIPE_PARAMETER_INVALID,
        "Filter value is incompatible with the temporal column.",
        422,
        details={
            "path": path,
            "column": column,
            "operator": operator,
            "value": value,
            "column_type": str(dtype),
            "expected_format": expected,
        },
    )
