from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import ErrorCode, RiskLevel
from app.core.exceptions import APIError
from app.models.masking import SensitiveDictionary
from app.repositories.sensitive_dictionary_repository import SensitiveDictionaryRepository


DEFAULT_SENSITIVE_RULE_TEMPLATES: tuple[dict[str, str | bool], ...] = (
    {
        "entity_type": "customer_name",
        "value": "Example Customer Corp",
        "replacement": "[CUSTOMER_001]",
        "risk_level": RiskLevel.MEDIUM.value,
        "is_active": False,
    },
    {
        "entity_type": "restricted_keyword",
        "value": "RESTRICTED_FORMULA",
        "replacement": "[BLOCK]",
        "risk_level": RiskLevel.HIGH.value,
        "is_active": False,
    },
    {
        "entity_type": "machine_name",
        "value": "Machine-A12",
        "replacement": "[MACHINE_001]",
        "risk_level": RiskLevel.MEDIUM.value,
        "is_active": False,
    },
)


class SensitiveDictionaryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = SensitiveDictionaryRepository(db)

    def list_rules(self) -> list[SensitiveDictionary]:
        return self.repository.list_all()

    def create_or_update_rule(
        self,
        *,
        entity_type: str,
        value: str,
        replacement: str | None,
        risk_level: RiskLevel,
        is_active: bool,
    ) -> SensitiveDictionary:
        existing = self.repository.find_existing(entity_type, value)
        if existing is not None:
            existing.replacement = replacement
            existing.risk_level = risk_level.value
            existing.is_active = is_active
            self.db.commit()
            self.db.refresh(existing)
            return existing

        rule = SensitiveDictionary(
            entity_type=entity_type,
            value=value,
            replacement=replacement,
            risk_level=risk_level.value,
            is_active=is_active,
        )
        self.repository.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def update_rule_status(self, rule_id: UUID, is_active: bool) -> SensitiveDictionary:
        rule = self.repository.get(rule_id)
        if rule is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Sensitive dictionary rule not found.", 404)
        rule.is_active = is_active
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def install_templates(self) -> list[SensitiveDictionary]:
        created_or_updated = [
            self.create_or_update_rule(
                entity_type=str(template["entity_type"]),
                value=str(template["value"]),
                replacement=str(template["replacement"]) if template["replacement"] is not None else None,
                risk_level=RiskLevel(str(template["risk_level"])),
                is_active=bool(template["is_active"]),
            )
            for template in DEFAULT_SENSITIVE_RULE_TEMPLATES
        ]
        return created_or_updated
