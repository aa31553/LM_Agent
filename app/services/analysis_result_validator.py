from __future__ import annotations

from typing import Any

from app.core.constants import ErrorCode
from app.core.exceptions import APIError


class AnalysisResultValidator:
    def validate(
        self,
        rows: list[dict[str, Any]],
        required_fields: list[str],
        *,
        recipe_id: str,
    ) -> list[dict[str, Any]]:
        required = set(required_fields)
        for index, row in enumerate(rows):
            missing = sorted(required - set(row))
            if missing:
                raise APIError(
                    ErrorCode.RESULT_SCHEMA_MISMATCH,
                    "Recipe result does not match its declared contract.",
                    500,
                    details={
                        "recipe_id": recipe_id,
                        "row_index": index,
                        "missing_fields": missing,
                    },
                )
        return rows
