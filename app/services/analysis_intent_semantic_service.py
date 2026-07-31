from __future__ import annotations

import re
from typing import Any

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.schemas.analysis import AnalysisPlan
from app.schemas.analysis_recipe import AnalysisClarification, AnalysisClarificationOption
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler


class AnalysisIntentSemanticService:
    """Validate analysis meaning after structural Recipe compilation."""

    AGGREGATION_TOKENS = {
        "count": (
            "count",
            "筆數",
            "資料列數",
            "記錄數",
            "紀錄數",
            "件數",
            "發生次數",
            "出現次數",
            "多少筆",
            "多少件",
        ),
        "mean": ("mean", "average", "avg", "平均", "均值"),
        "sum": ("sum", "加總", "總和", "合計", "累計"),
        "median": ("median", "中位數", "中位值"),
        "min": ("min", "minimum", "最小", "最低"),
        "max": ("max", "maximum", "最大", "最高"),
    }
    SUMMARY_RECIPES = {"category_summary", "trend_summary", "group_summary"}
    VALUE_AGGREGATIONS = ("mean", "sum", "max", "min", "median")
    OPTION_LABELS = {
        "count": ("資料筆數", "計算每個期間內的資料列數，不讀取量測值。"),
        "mean": ("平均值", "呈現各期間量測值的平均水準。"),
        "sum": ("合計值", "呈現各期間量測值的總和。"),
        "max": ("最大值", "呈現各期間量測值的最高值。"),
        "min": ("最小值", "呈現各期間量測值的最低值。"),
        "median": ("中位數", "呈現各期間量測值的中位數，較不受極端值影響。"),
    }

    def validate_explicit(self, question: str, plan: AnalysisPlan) -> None:
        if plan.recipe_id not in self.SUMMARY_RECIPES:
            return
        requested = self.explicit_aggregation(question)
        actual = plan.recipe_inputs.get("aggregation")
        if requested is None or actual == requested:
            return
        raise APIError(
            ErrorCode.ANALYSIS_INTENT_INVALID,
            "The generated aggregation does not match the user's explicit request.",
            422,
            details={
                "path": "inputs.aggregation",
                "requested_aggregation": requested,
                "generated_aggregation": actual,
                "repair_hint": f"Set inputs.aggregation to {requested}.",
            },
        )

    def clarification(
        self,
        question: str,
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> AnalysisClarification | None:
        if plan.recipe_id not in self.SUMMARY_RECIPES:
            return None
        if self.explicit_aggregation(question) is not None:
            return None

        inputs = plan.recipe_inputs
        value_field = inputs.get("value_field")
        referenced_numeric = self._referenced_numeric_field(question, plan, manifests)
        if value_field is None and referenced_numeric is None:
            return None

        selected_field = value_field or referenced_numeric
        options = [
            self._option(aggregation, selected_field)
            for aggregation in (*self.VALUE_AGGREGATIONS, "count")
        ]
        field_label = selected_field or "數值欄位"
        return AnalysisClarification(
            code="AGGREGATION_REQUIRED",
            parameter="aggregation",
            question=f"「{field_label}」要如何彙總？",
            reason=(
                "需求指定了數值趨勢，但未明確說明要計算平均、合計、最大、"
                "最小、中位數或資料筆數；系統不應自行猜測。"
            ),
            options=options,
        )

    @classmethod
    def explicit_aggregation(cls, question: str) -> str | None:
        normalized = question.casefold()
        matches = {
            aggregation
            for aggregation, tokens in cls.AGGREGATION_TOKENS.items()
            if any(cls._contains_token(normalized, token) for token in tokens)
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _contains_token(question: str, token: str) -> bool:
        if token.isascii() and token.isalpha():
            return re.search(rf"\b{re.escape(token)}\b", question) is not None
        return token in question

    @classmethod
    def apply_choice(
        cls,
        plan: AnalysisPlan,
        clarification: AnalysisClarification,
        choice: str,
    ) -> AnalysisPlan:
        option = next((item for item in clarification.options if item.value == choice), None)
        if option is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The clarification choice is not one of the allowed options.",
                422,
                details={"allowed_choices": [item.value for item in clarification.options]},
            )
        inputs = dict(plan.recipe_inputs)
        for key, value in option.recipe_inputs_patch.items():
            if value is None:
                inputs.pop(key, None)
            else:
                inputs[key] = value
        return plan.model_copy(update={"recipe_inputs": inputs})

    @classmethod
    def _option(
        cls,
        aggregation: str,
        value_field: str | None,
    ) -> AnalysisClarificationOption:
        label, description = cls.OPTION_LABELS[aggregation]
        patch: dict[str, Any] = {"aggregation": aggregation}
        if aggregation == "count":
            patch["value_field"] = None
        elif value_field is not None:
            patch["value_field"] = value_field
        return AnalysisClarificationOption(
            value=aggregation,
            label=label,
            description=description,
            recipe_inputs_patch=patch,
        )

    @staticmethod
    def _referenced_numeric_field(
        question: str,
        plan: AnalysisPlan,
        manifests: dict[str, dict[str, Any]],
    ) -> str | None:
        normalized_question = question.casefold()
        candidates: list[tuple[int, str]] = []
        for source in plan.sources:
            manifest = manifests.get(str(source.file_id), {})
            try:
                dataset = AnalysisRecipeCompiler._select_dataset(manifest, source.sheet)
            except APIError:
                continue
            for column in dataset.get("columns", []):
                if column.get("inferred_type") not in {"integer", "number"}:
                    continue
                names = {
                    str(column.get("name") or ""),
                    str(column.get("display_name") or ""),
                    str(column.get("normalized_name") or ""),
                }
                matches = [
                    name
                    for name in names
                    if len(name.strip()) >= 2 and name.casefold() in normalized_question
                ]
                if matches:
                    best = max(matches, key=len)
                    candidates.append(
                        (len(best), f"{source.alias}.{column.get('name')}")
                    )
        return max(candidates, default=(0, None), key=lambda item: item[0])[1]
