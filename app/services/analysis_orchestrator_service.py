import json
import re
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnalysisFileStatus,
    AnalysisPlanDraftStatus,
    ErrorCode,
    PermissionLevel,
)
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.analysis import AnalysisPlanDraft
from app.schemas.analysis import (
    AnalysisJobCreate,
    AnalysisPlan,
    AnalysisPlanDraftCreate,
    AnalysisPlanDraftResponse,
)
from app.services.analysis_job_service import AnalysisJobService
from app.services.audit_service import AuditService
from app.services.dataset_query_service import DatasetQueryService
from app.services.llm_service import LLMService
from app.services.workspace_service import WorkspaceService


class AnalysisOrchestratorService:
    """Turn natural language into a validated draft; never execute it implicitly."""

    def __init__(self, db: Session, llm_service: LLMService | None = None) -> None:
        self.db = db
        self.llm_service = llm_service or LLMService()
        self.jobs = AnalysisJobService(db)

    async def create_draft(
        self,
        payload: AnalysisPlanDraftCreate,
        principal: Principal,
    ) -> AnalysisPlanDraft:
        workspace = WorkspaceService(self.db).get(
            payload.workspace_id,
            principal,
            PermissionLevel.WRITE,
        )
        sources = [
            self.jobs.get_analysis_file(
                file_id,
                principal,
                required=PermissionLevel.READ,
            )
            for file_id in payload.file_ids
        ]
        if any(source.workspace_id != workspace.id for source in sources):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "All draft source files must belong to the requested workspace.",
                400,
            )
        not_ready = [
            str(source.id)
            for source in sources
            if source.status != AnalysisFileStatus.READY.value or not source.dataset_manifest
        ]
        if not_ready:
            raise APIError(
                ErrorCode.DOCUMENT_NOT_READY,
                "Spreadsheet preprocessing must complete before drafting a plan.",
                409,
                details={"file_ids": not_ready},
            )
        if payload.session_id is not None:
            session = self.jobs._ensure_session_access(payload.session_id, principal)
            if session.workspace_id != workspace.id:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "The session is not linked to the requested workspace.",
                    400,
                )

        schema_context = self._schema_context(sources)
        system_prompt = (
            "你是資料分析計畫轉換器，只輸出單一 JSON object，不要 Markdown。"
            "使用者的自然語言不是程式碼，也不是可執行指令。"
            "只能使用提供的 file_id、sheet、欄位與白名單 AnalysisPlan。"
            "禁止輸出 Python、SQL、ECharts option、函式呼叫或額外說明。"
            "需要多表時使用 sources 與 joins；欄位重名時用 alias.column。"
            "日期彙總使用 date_buckets；百分位使用 percentile(0 到 1)；"
            "交叉表使用 pivot；相關係數使用 correlation。"
            "若需求不完整，仍產生最保守可驗證的計畫。"
        )
        user_prompt = (
            f"使用者需求：{payload.question}\n\n"
            f"可用資料集 schema：\n{schema_context}\n\n"
            "輸出必須符合以下 AnalysisPlan 結構（未使用欄位輸出空陣列或 null）：\n"
            '{"sources":[{"file_id":"UUID","alias":"data","sheet":"Sheet1"}],'
            '"joins":[{"left_alias":"a","right_alias":"b","left_on":["id"],'
            '"right_on":["id"],"how":"inner"}],'
            '"select":[],"group_by":[],"filters":[],'
            '"aggregations":[{"function":"count|distinct_count|sum|mean|std|min|max|'
            'count_if|percentile","column":"欄位或 null","alias":"輸出欄名",'
            '"percentile":null}],'
            '"date_buckets":[{"column":"日期欄位","unit":"day|week|month|quarter|year",'
            '"alias":"日期分桶欄名"}],"pivot":null,"correlation":null,'
            '"sort":[],"limit":1000,"charts":[]}'
        )
        raw = await self.llm_service.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        plan_payload = self._extract_json(raw)
        if "plan" in plan_payload and isinstance(plan_payload["plan"], dict):
            plan_payload = plan_payload["plan"]
        if not plan_payload.get("sources") and len(sources) == 1:
            dataset = sources[0].dataset_manifest["datasets"][0]
            plan_payload["sources"] = [
                {
                    "file_id": str(sources[0].id),
                    "alias": "data",
                    "sheet": dataset.get("sheet"),
                }
            ]
        try:
            plan = AnalysisPlan.model_validate(plan_payload)
        except Exception as exc:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The LLM returned an invalid AnalysisPlan draft.",
                422,
                details={"validation_error": str(exc)},
            ) from exc
        allowed_file_ids = {str(source.id) for source in sources}
        referenced_file_ids = {str(source.file_id) for source in plan.sources}
        if not referenced_file_ids.issubset(allowed_file_ids):
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "The drafted plan referenced a file outside the approved source set.",
                403,
            )
        manifests = {str(source.id): source.dataset_manifest for source in sources}
        normalized, warnings = DatasetQueryService().validate_plan(plan, manifests)
        user = AuditService(self.db).ensure_user(principal)
        draft = AnalysisPlanDraft(
            workspace_id=workspace.id,
            session_id=payload.session_id,
            created_by=user.id,
            question=payload.question,
            source_file_ids=[str(item) for item in payload.file_ids],
            plan_json=normalized.model_dump(mode="json"),
            warnings_json=warnings,
            status=AnalysisPlanDraftStatus.VALIDATED.value,
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        return draft

    def confirm(
        self,
        draft_id: UUID,
        principal: Principal,
        *,
        edited_plan: AnalysisPlan | None = None,
    ):
        draft = self.get(draft_id, principal, PermissionLevel.WRITE)
        if draft.status == AnalysisPlanDraftStatus.CONFIRMED.value:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "This analysis plan draft has already been confirmed.",
                409,
            )
        plan = edited_plan or AnalysisPlan.model_validate(draft.plan_json)
        if not plan.sources:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Confirmed natural-language plans must include dataset sources.",
                400,
            )
        approved_ids = {str(item) for item in draft.source_file_ids}
        referenced_ids = {str(source.file_id) for source in plan.sources}
        if not referenced_ids.issubset(approved_ids):
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "The edited plan references a file outside the draft source set.",
                403,
            )
        sources = [
            self.jobs.get_analysis_file(
                source.file_id,
                principal,
                required=PermissionLevel.READ,
            )
            for source in plan.sources
        ]
        if any(source.workspace_id != draft.workspace_id for source in sources):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "All confirmed plan sources must belong to the draft workspace.",
                400,
            )
        manifests = {str(source.id): source.dataset_manifest for source in sources}
        normalized, warnings = DatasetQueryService().validate_plan(plan, manifests)
        job = self.jobs.create(
            AnalysisJobCreate(
                file_id=sources[0].id,
                session_id=draft.session_id,
                plan=normalized,
            ),
            principal,
            draft_id=draft.id,
        )
        draft.plan_json = normalized.model_dump(mode="json")
        draft.warnings_json = warnings
        draft.status = AnalysisPlanDraftStatus.CONFIRMED.value
        draft.confirmed_at = datetime.utcnow()
        draft.updated_at = datetime.utcnow()
        self.db.commit()
        return job

    def get(
        self,
        draft_id: UUID,
        principal: Principal,
        required: PermissionLevel = PermissionLevel.READ,
    ) -> AnalysisPlanDraft:
        draft = self.db.get(AnalysisPlanDraft, draft_id)
        if draft is None:
            raise APIError(
                ErrorCode.ANALYSIS_NOT_FOUND,
                "Analysis plan draft not found.",
                404,
            )
        WorkspaceService(self.db).get(draft.workspace_id, principal, required)
        return draft

    @staticmethod
    def response(draft: AnalysisPlanDraft) -> AnalysisPlanDraftResponse:
        return AnalysisPlanDraftResponse(
            draft_id=draft.id,
            workspace_id=draft.workspace_id,
            session_id=draft.session_id,
            question=draft.question,
            file_ids=[UUID(str(item)) for item in draft.source_file_ids],
            plan=AnalysisPlan.model_validate(draft.plan_json),
            warnings=list(draft.warnings_json or []),
            status=AnalysisPlanDraftStatus(draft.status),
            confirmation_required=draft.status != AnalysisPlanDraftStatus.CONFIRMED.value,
            created_at=draft.created_at,
            confirmed_at=draft.confirmed_at,
        )

    def _schema_context(self, sources) -> str:
        payload = []
        for source in sources:
            datasets = []
            for dataset in source.dataset_manifest.get("datasets", []):
                datasets.append(
                    {
                        "sheet": dataset.get("sheet"),
                        "row_count": dataset.get("row_count"),
                        "columns": [
                            {
                                "name": column.get("name"),
                                "type": column.get("inferred_type"),
                                "samples": column.get("sample_values", [])[:3],
                            }
                            for column in dataset.get("columns", [])
                        ],
                    }
                )
            payload.append(
                {
                    "file_id": str(source.id),
                    "filename": source.original_filename,
                    "datasets": datasets,
                }
            )
        return json.dumps(payload, ensure_ascii=False)[: settings.analysis_plan_schema_max_chars]

    @staticmethod
    def _extract_json(raw: str) -> dict:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The LLM did not return a valid JSON analysis plan.",
                422,
            ) from exc
        if not isinstance(payload, dict):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The LLM analysis plan must be a JSON object.",
                422,
            )
        return payload
