import json
from typing import Protocol

from app.core.config import settings


class AnalysisPlanRepairLLM(Protocol):
    async def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


class AnalysisPlanRepairService:
    """Request at most one constrained repair; this service never executes a plan."""

    def __init__(self, llm_service: AnalysisPlanRepairLLM) -> None:
        self.llm_service = llm_service

    async def repair(
        self,
        plan_payload: dict,
        *,
        validation_error: str,
        schema_context: str,
        contract: str = "plan",
    ) -> str:
        contract_name = "IntentDraft" if contract == "intent" else "AnalysisPlan"
        system_prompt = (
            f"你是 {contract_name} JSON 修復器，只輸出單一 JSON object，不要 Markdown。"
            "只修復驗證錯誤，不得新增未提供的檔案、工作表或欄位。"
            "禁止輸出 Python、SQL、JavaScript、ECharts option、路徑或額外說明。"
            "這是唯一一次修復機會。"
        )
        repair_context = {
            "invalid_plan": plan_payload,
            "validation_error": validation_error,
            "available_schema": schema_context[: settings.analysis_plan_schema_max_chars // 2],
        }
        user_prompt = (
            f"請修復下列 {contract_name}。保留使用者意圖；"
            + (
                "只選擇白名單 Recipe 與參數，不要輸出圖表欄位。"
                "count 聚合必須移除 value_field；mean/sum/min/max/median "
                "聚合必須保留 numeric value_field。"
                "filter operator 只允許 eq/ne/gt/gte/lt/lte/contains/in/"
                "is_null/not_null；日期範圍使用 gte 起日與 lt 迄日後一天，"
                "不得使用 between。"
                "嚴格依 validation_error.details 的 path 與 repair_hint 修復：\n"
                if contract == "intent"
                else "並符合目前的 x_field/y_field 圖表契約：\n"
            )
            + json.dumps(repair_context, ensure_ascii=False)
        )
        return await self.llm_service.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt[: settings.analysis_plan_schema_max_chars],
        )
