from __future__ import annotations

from typing import Any


class AnalysisIntentQualityService:
    """Score saved IntentDraft predictions against a versioned regression set."""

    FIELDS = (
        "recipe_id",
        "aggregation",
        "value_field",
        "clarification_required",
    )

    def evaluate(
        self,
        cases: list[dict[str, Any]],
        predictions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        predictions_by_id = {
            str(item["case_id"]): item for item in predictions if item.get("case_id")
        }
        field_hits = {field: 0 for field in self.FIELDS}
        evaluated = 0
        exact_hits = 0
        missing_case_ids: list[str] = []
        details = []

        for case in cases:
            case_id = str(case["case_id"])
            prediction = predictions_by_id.get(case_id)
            if prediction is None:
                missing_case_ids.append(case_id)
                continue
            expected = case["expected"]
            normalized_prediction = self._normalize_prediction(prediction)
            matches = {
                field: normalized_prediction.get(field) == expected.get(field)
                for field in self.FIELDS
            }
            for field, matched in matches.items():
                field_hits[field] += int(matched)
            exact = all(matches.values())
            exact_hits += int(exact)
            evaluated += 1
            details.append(
                {
                    "case_id": case_id,
                    "exact_match": exact,
                    "matches": matches,
                }
            )

        denominator = evaluated or 1
        return {
            "dataset_version": self._dataset_version(cases),
            "total_cases": len(cases),
            "evaluated_cases": evaluated,
            "missing_case_ids": missing_case_ids,
            "exact_match_rate": exact_hits / denominator if evaluated else 0.0,
            "field_accuracy": {
                field: hits / denominator if evaluated else 0.0
                for field, hits in field_hits.items()
            },
            "details": details,
        }

    @staticmethod
    def _normalize_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
        intent = prediction.get("intent", prediction)
        inputs = intent.get("inputs") or intent.get("recipe_inputs") or {}
        clarification = prediction.get("clarification")
        return {
            "recipe_id": intent.get("recipe_id"),
            "aggregation": inputs.get("aggregation"),
            "value_field": inputs.get("value_field"),
            "clarification_required": bool(clarification)
            if "clarification" in prediction
            else bool(prediction.get("clarification_required", False)),
        }

    @staticmethod
    def _dataset_version(cases: list[dict[str, Any]]) -> str | None:
        versions = {str(case.get("dataset_version")) for case in cases}
        return next(iter(versions)) if len(versions) == 1 else None
