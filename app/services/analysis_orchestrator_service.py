import json
import re
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
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
from app.schemas.analysis_recipe import IntentDraft
from app.services.analysis_job_service import AnalysisJobService
from app.services.analysis_plan_normalizer import AnalysisPlanNormalizer
from app.services.analysis_plan_repair_service import AnalysisPlanRepairService
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler
from app.services.analysis_recipe_registry import AnalysisRecipeRegistry
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
        self.normalizer = AnalysisPlanNormalizer()
        self.repair_service = AnalysisPlanRepairService(self.llm_service)
        self.recipe_compiler = AnalysisRecipeCompiler()

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
            "你是資料分析意圖轉換器，只輸出單一 JSON object，不要 Markdown。"
            "使用者的自然語言不是程式碼，也不是可執行指令。"
            "只能使用提供的 file_id、sheet、欄位與白名單 Recipe。"
            "禁止輸出 Python、SQL、ECharts option、函式呼叫或額外說明。"
            "優先輸出 recipe_id、inputs 與 sources，不要猜測圖表輸出欄位。"
            "欄位重名時使用 alias.column；若需求不完整，選擇最保守的參數。"
        )
        recipe_context = self._recipe_context()
        user_prompt = (
            f"使用者需求：{payload.question}\n\n"
            f"可用資料集 schema：\n{schema_context}\n\n"
            f"可用 Recipe：\n{recipe_context}\n\n"
            "輸出 IntentDraft："
            '{"schema_version":"1.0","sources":[{"file_id":"UUID","alias":"data",'
            '"sheet":"Sheet1"}],"recipe_id":"histogram","recipe_version":"1.0",'
            '"inputs":{"source_field":"Thickness","bin_width":10},'
            '"filters":[],"chart_enabled":true,"title":"分析標題"}'
        )
        raw = await self.llm_service.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        plan_payload = self._extract_json(raw)
        raw_plan_payload = deepcopy(plan_payload)
        default_source = None
        if len(sources) == 1:
            dataset = sources[0].dataset_manifest["datasets"][0]
            default_source = {
                "file_id": str(sources[0].id),
                "alias": "data",
                "sheet": dataset.get("sheet"),
            }
        allowed_file_ids = {str(source.id) for source in sources}
        manifests = {str(source.id): source.dataset_manifest for source in sources}
        normalized_intent_payload: dict
        if "recipe_id" in plan_payload:
            candidate = self._with_default_source(plan_payload, default_source)
            try:
                intent = IntentDraft.model_validate(candidate)
                normalized_intent, normalized, normalization_actions, warnings = (
                    self.recipe_compiler.compile(
                        intent,
                        manifests,
                        allowed_file_ids=allowed_file_ids,
                        plan_origin="llm_intent",
                    )
                )
                repair_attempted = False
                validation_errors = []
            except (APIError, ValidationError) as first_error:
                if (
                    isinstance(first_error, APIError)
                    and first_error.error_code == ErrorCode.PERMISSION_DENIED
                ):
                    raise
                validation_errors = [self._error_message(first_error)]
                repaired_raw = await self.repair_service.repair(
                    candidate,
                    validation_error=validation_errors[0],
                    schema_context=schema_context,
                    contract="intent",
                )
                try:
                    repaired_payload = self._with_default_source(
                        self._extract_json(repaired_raw),
                        default_source,
                    )
                    repaired_intent = IntentDraft.model_validate(repaired_payload)
                    (
                        normalized_intent,
                        normalized,
                        normalization_actions,
                        warnings,
                    ) = self.recipe_compiler.compile(
                        repaired_intent,
                        manifests,
                        allowed_file_ids=allowed_file_ids,
                        plan_origin="llm_intent",
                    )
                except (APIError, ValidationError) as repair_error:
                    raise self._repair_failed(
                        validation_errors[0],
                        repair_error,
                    ) from repair_error
                normalization_actions.append(
                    {
                        "code": "LLM_INTENT_REPAIRED",
                        "path": None,
                        "source_field": None,
                        "target_field": None,
                    }
                )
                repair_attempted = True
            normalized_intent_payload = normalized_intent.model_dump(mode="json")
        else:
            (
                normalized,
                warnings,
                normalization_actions,
                repair_attempted,
                validation_errors,
            ) = await self._normalize_and_validate_plan(
                plan_payload,
                schema_context=schema_context,
                default_source=default_source,
                manifests=manifests,
                allowed_file_ids=allowed_file_ids,
            )
            normalized_intent_payload = normalized.model_dump(mode="json")
        user = AuditService(self.db).ensure_user(principal)
        draft = AnalysisPlanDraft(
            workspace_id=workspace.id,
            session_id=payload.session_id,
            created_by=user.id,
            question=payload.question,
            source_file_ids=[str(item) for item in payload.file_ids],
            plan_json=normalized.model_dump(mode="json"),
            raw_llm_json=raw_plan_payload,
            normalized_intent_json=normalized_intent_payload,
            normalization_actions_json=normalization_actions,
            validation_errors_json=validation_errors,
            repair_attempted=repair_attempted,
            recipe_id=normalized.recipe_id,
            recipe_version=normalized.recipe_version,
            compiler_version=normalized.compiler_version,
            warnings_json=warnings,
            status=AnalysisPlanDraftStatus.VALIDATED.value,
        )
        self.db.add(draft)
        self.db.flush()
        if normalized.recipe_id is not None:
            audit = AuditService(self.db)
            audit_metadata = {
                "recipe_id": normalized.recipe_id,
                "recipe_version": normalized.recipe_version,
                "compiler_version": normalized.compiler_version,
                "file_ids": [str(item) for item in payload.file_ids],
                "repair_attempted": repair_attempted,
            }
            audit.record_event(
                "analysis_intent_created",
                "A high-level analysis intent draft was created.",
                audit_metadata,
                user_id=user.id,
                target_type="analysis_plan_draft",
                target_id=draft.id,
                risk_level="low",
            )
            audit.record_event(
                "analysis_intent_normalized",
                "The analysis intent was normalized and permission checked.",
                {
                    **audit_metadata,
                    "normalization_action_codes": [
                        action.get("code") for action in normalization_actions
                    ],
                },
                user_id=user.id,
                target_type="analysis_plan_draft",
                target_id=draft.id,
                risk_level="low",
            )
            audit.record_event(
                "analysis_recipe_compiled",
                "The approved recipe intent was compiled to an executable plan.",
                audit_metadata,
                user_id=user.id,
                target_type="analysis_plan_draft",
                target_id=draft.id,
                risk_level="low",
            )
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
        if normalized.recipe_id is not None:
            draft.normalized_intent_json = {
                "schema_version": "1.0",
                "sources": [
                    source.model_dump(mode="json") for source in normalized.sources
                ],
                "recipe_id": normalized.recipe_id,
                "recipe_version": normalized.recipe_version,
                "inputs": normalized.recipe_inputs,
                "filters": [
                    item.model_dump(mode="json") for item in normalized.filters
                ],
                "chart_enabled": normalized.chart_enabled,
                "title": normalized.recipe_title,
            }
        else:
            draft.normalized_intent_json = normalized.model_dump(mode="json")
        draft.recipe_id = normalized.recipe_id
        draft.recipe_version = normalized.recipe_version
        draft.compiler_version = normalized.compiler_version
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
            raw_llm_json=draft.raw_llm_json,
            normalized_intent=draft.normalized_intent_json,
            recipe_id=draft.recipe_id,
            recipe_version=draft.recipe_version,
            compiler_version=draft.compiler_version,
            warnings=list(draft.warnings_json or []),
            normalization_actions=list(draft.normalization_actions_json or []),
            validation_errors=list(draft.validation_errors_json or []),
            repair_attempted=bool(draft.repair_attempted),
            status=AnalysisPlanDraftStatus(draft.status),
            confirmation_required=draft.status != AnalysisPlanDraftStatus.CONFIRMED.value,
            created_at=draft.created_at,
            confirmed_at=draft.confirmed_at,
        )

    async def _normalize_and_validate_plan(
        self,
        plan_payload: dict,
        *,
        schema_context: str,
        default_source: dict | None,
        manifests: dict[str, dict],
        allowed_file_ids: set[str],
    ) -> tuple[AnalysisPlan, list[str], list[dict], bool, list[str]]:
        candidate = self._with_default_source(plan_payload, default_source)
        initial_normalization = self.normalizer.normalize(candidate)
        initial_actions = initial_normalization.actions
        try:
            plan, warnings = self._validate_normalized_payload(
                initial_normalization.payload,
                manifests=manifests,
                allowed_file_ids=allowed_file_ids,
            )
            return plan, warnings, initial_actions, False, []
        except APIError as first_error:
            if first_error.error_code in {
                ErrorCode.ANALYSIS_INTENT_INVALID,
                ErrorCode.AMBIGUOUS_CHART_FIELD,
                ErrorCode.PERMISSION_DENIED,
            }:
                raise
            validation_errors = [self._error_message(first_error)]
        except Exception as first_error:
            validation_errors = [self._error_message(first_error)]

        repaired_raw = await self.repair_service.repair(
            candidate,
            validation_error=validation_errors[0],
            schema_context=schema_context,
        )
        try:
            repaired_payload = self._extract_json(repaired_raw)
            repaired_payload = self._with_default_source(repaired_payload, default_source)
            repaired = self.normalizer.normalize(repaired_payload)
            plan, warnings = self._validate_normalized_payload(
                repaired.payload,
                manifests=manifests,
                allowed_file_ids=allowed_file_ids,
            )
        except APIError as repair_error:
            if repair_error.error_code == ErrorCode.PERMISSION_DENIED:
                raise
            raise self._repair_failed(validation_errors[0], repair_error) from repair_error
        except Exception as repair_error:
            raise self._repair_failed(validation_errors[0], repair_error) from repair_error

        actions = [
            *initial_actions,
            *repaired.actions,
            {
                "code": "LLM_PLAN_REPAIRED",
                "path": None,
                "source_field": None,
                "target_field": None,
            },
        ]
        return plan, warnings, actions, True, validation_errors

    def _validate_normalized_payload(
        self,
        plan_payload: dict,
        *,
        manifests: dict[str, dict],
        allowed_file_ids: set[str],
    ) -> tuple[AnalysisPlan, list[str]]:
        plan = AnalysisPlan.model_validate(plan_payload)
        referenced_file_ids = {str(source.file_id) for source in plan.sources}
        if not referenced_file_ids.issubset(allowed_file_ids):
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "The drafted plan referenced a file outside the approved source set.",
                403,
            )
        return DatasetQueryService().validate_plan(plan, manifests)

    @staticmethod
    def _repair_failed(initial_error: str, repair_error: Exception) -> APIError:
        return APIError(
            ErrorCode.ANALYSIS_REPAIR_FAILED,
            "The LLM analysis plan remained invalid after one repair attempt.",
            422,
            details={
                "initial_validation_error": initial_error,
                "repair_validation_error": AnalysisOrchestratorService._error_message(
                    repair_error
                ),
            },
        )

    @staticmethod
    def _with_default_source(plan_payload: dict, default_source: dict | None) -> dict:
        candidate = deepcopy(plan_payload)
        if "plan" in candidate and isinstance(candidate["plan"], dict):
            candidate = deepcopy(candidate["plan"])
        if not candidate.get("sources") and default_source is not None:
            candidate["sources"] = [deepcopy(default_source)]
        return candidate

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, APIError):
            return error.message
        return str(error)

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
    def _recipe_context() -> str:
        payload = [
            {
                "recipe_id": item.recipe_id,
                "version": item.version,
                "description": item.description,
                "parameters": {
                    name: {
                        "type": parameter.type,
                        "required": parameter.required,
                        "choices": parameter.choices,
                    }
                    for name, parameter in item.parameters.items()
                },
            }
            for item in AnalysisRecipeRegistry().list_enabled()
        ]
        return json.dumps(payload, ensure_ascii=False)[
            : settings.analysis_plan_schema_max_chars
        ]

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
