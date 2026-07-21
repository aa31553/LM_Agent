import math
import time
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.integrations.embedding_client import EmbeddingClient
from app.integrations.openai_compatible_client import build_api_url
from app.schemas.admin import (
    EmbeddingStatusResponse,
    EmbeddingTestRequest,
    EmbeddingTestResponse,
    LLMTestRequest,
    LLMTestResponse,
    OperationMetricsResponse,
    RetentionPolicyRequest,
    RetentionPolicyResponse,
    SensitiveRuleRequest,
    SensitiveRuleResponse,
    SensitiveRuleStatusRequest,
)
from app.services.embedding_service import EmbeddingService
from app.services.operation_monitoring_service import OperationMonitoringService
from app.services.retention_service import RetentionService
from app.services.sensitive_dictionary_service import SensitiveDictionaryService
from app.services.llm_service import LLMService

router = APIRouter()


@router.get("/status")
async def admin_status(principal: Principal = Depends(get_current_principal)) -> dict[str, str]:
    _ensure_admin(principal)
    return {"status": "ok"}


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(
    payload: LLMTestRequest,
    principal: Principal = Depends(get_current_principal),
) -> LLMTestResponse:
    """Test the configured LLM without initializing database-backed RAG services."""

    _ensure_admin(principal)
    started_at = time.perf_counter()
    answer = await LLMService().complete(
        system_prompt=payload.system_prompt,
        user_prompt=payload.message,
        image_paths=[],
    )
    return LLMTestResponse(
        status="ok",
        model=settings.llm_model,
        endpoint=build_api_url(settings.llm_base_url, settings.llm_api_path),
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        answer=answer,
    )


@router.get("/embedding/status", response_model=EmbeddingStatusResponse)
async def embedding_status(
    principal: Principal = Depends(get_current_principal),
) -> EmbeddingStatusResponse:
    _ensure_admin(principal)
    service_status = await EmbeddingClient().status()
    return EmbeddingStatusResponse(
        status=str(service_status.get("status", "unknown")),
        endpoint=settings.embedding_endpoint,
        configured_model=settings.embedding_model,
        configured_dimension=settings.embedding_dimension,
        service=service_status,
    )


@router.post("/embedding/test", response_model=EmbeddingTestResponse)
async def test_embedding_connection(
    payload: EmbeddingTestRequest,
    principal: Principal = Depends(get_current_principal),
) -> EmbeddingTestResponse:
    _ensure_admin(principal)
    started_at = time.perf_counter()
    vectors = await EmbeddingService().embed_texts([payload.text])
    vector = vectors[0]
    return EmbeddingTestResponse(
        status="ok",
        model=settings.embedding_model,
        endpoint=settings.embedding_endpoint,
        configured_dimension=settings.embedding_dimension,
        actual_dimension=len(vector),
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        vector_norm=round(math.sqrt(sum(value * value for value in vector)), 6),
        vector_preview=[round(value, 6) for value in vector[:8]],
    )


@router.get("/operations/metrics", response_model=OperationMetricsResponse)
async def operation_metrics(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> OperationMetricsResponse:
    _ensure_admin(principal)
    return OperationMonitoringService(db).metrics()


@router.post("/retention", response_model=RetentionPolicyResponse)
async def run_retention_policy(
    payload: RetentionPolicyRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> RetentionPolicyResponse:
    _ensure_admin(principal)
    return RetentionService(db).enforce(payload)


@router.get("/sensitive-rules", response_model=dict[str, list[SensitiveRuleResponse]])
async def list_sensitive_rules(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[SensitiveRuleResponse]]:
    _ensure_admin(principal)
    return {
        "items": [
            _sensitive_rule_response(rule)
            for rule in SensitiveDictionaryService(db).list_rules()
        ]
    }


@router.post("/sensitive-rules", response_model=SensitiveRuleResponse)
async def upsert_sensitive_rule(
    payload: SensitiveRuleRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SensitiveRuleResponse:
    _ensure_admin(principal)
    rule = SensitiveDictionaryService(db).create_or_update_rule(
        entity_type=payload.entity_type,
        value=payload.value,
        replacement=payload.replacement,
        risk_level=payload.risk_level,
        is_active=payload.is_active,
    )
    return _sensitive_rule_response(rule)


@router.patch("/sensitive-rules/{rule_id}", response_model=SensitiveRuleResponse)
async def update_sensitive_rule_status(
    rule_id: UUID,
    payload: SensitiveRuleStatusRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SensitiveRuleResponse:
    _ensure_admin(principal)
    rule = SensitiveDictionaryService(db).update_rule_status(rule_id, payload.is_active)
    return _sensitive_rule_response(rule)


@router.post("/sensitive-rules/templates", response_model=dict[str, list[SensitiveRuleResponse]])
async def install_sensitive_rule_templates(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict[str, list[SensitiveRuleResponse]]:
    _ensure_admin(principal)
    return {
        "items": [
            _sensitive_rule_response(rule)
            for rule in SensitiveDictionaryService(db).install_templates()
        ]
    }


def _ensure_admin(principal: Principal) -> None:
    if "admin" not in principal.roles:
        raise APIError(ErrorCode.PERMISSION_DENIED, "Admin role is required.", status_code=403)


def _sensitive_rule_response(rule) -> SensitiveRuleResponse:
    return SensitiveRuleResponse(
        rule_id=str(rule.id),
        entity_type=rule.entity_type,
        value=rule.value,
        replacement=rule.replacement,
        risk_level=rule.risk_level,
        is_active=rule.is_active,
    )
