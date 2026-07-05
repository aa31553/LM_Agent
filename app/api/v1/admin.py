from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.schemas.admin import (
    OperationMetricsResponse,
    RetentionPolicyRequest,
    RetentionPolicyResponse,
    SensitiveRuleRequest,
    SensitiveRuleResponse,
    SensitiveRuleStatusRequest,
)
from app.services.operation_monitoring_service import OperationMonitoringService
from app.services.retention_service import RetentionService
from app.services.sensitive_dictionary_service import SensitiveDictionaryService

router = APIRouter()


@router.get("/status")
async def admin_status(principal: Principal = Depends(get_current_principal)) -> dict[str, str]:
    _ensure_admin(principal)
    return {"status": "ok"}


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
