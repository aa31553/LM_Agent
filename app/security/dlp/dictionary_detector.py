from re import escape, finditer

from sqlalchemy.orm import Session

from app.core.constants import DLPAction, RiskLevel
from app.models.masking import SensitiveDictionary
from app.repositories.sensitive_dictionary_repository import SensitiveDictionaryRepository
from app.security.dlp.base import DLPFinding


DEFAULT_RULES: tuple[tuple[str, str, str, RiskLevel], ...] = (
    ("restricted_keyword", "RESTRICTED_FORMULA", "[BLOCK]", RiskLevel.HIGH),
    ("customer_name", "Example Customer Corp", "[CUSTOMER_001]", RiskLevel.MEDIUM),
    ("machine_name", "Machine-A12", "[MACHINE_001]", RiskLevel.MEDIUM),
)


class DictionaryDetector:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def detect(self, text: str) -> list[DLPFinding]:
        findings: list[DLPFinding] = []
        for rule in self._rules():
            if not rule.value:
                continue
            for match in finditer(escape(rule.value), text, flags=2):
                replacement = rule.replacement or f"[{rule.entity_type.upper()}]"
                action = DLPAction.BLOCK if replacement == "[BLOCK]" else DLPAction.MASK
                findings.append(
                    DLPFinding(
                        entity_type=rule.entity_type,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        action=action,
                        risk_level=RiskLevel(rule.risk_level),
                        replacement=replacement,
                    )
                )
        return findings

    def _rules(self) -> list[SensitiveDictionary]:
        if self.db is not None:
            return SensitiveDictionaryRepository(self.db).list_active()
        return [
            SensitiveDictionary(
                entity_type=entity_type,
                value=value,
                replacement=replacement,
                risk_level=risk_level.value,
                is_active=True,
            )
            for entity_type, value, replacement, risk_level in DEFAULT_RULES
        ]
