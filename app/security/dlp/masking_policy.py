from app.core.constants import DLPAction, RiskLevel
from app.schemas.chat import MaskedEntity
from app.security.dlp.base import DLPFinding, DLPResult


class MaskingPolicy:
    def apply(self, text: str, findings: list[DLPFinding], location: str) -> DLPResult:
        if not findings:
            return DLPResult(text=text)

        masked_entities: list[MaskedEntity] = []
        masked_text = text
        blocked = any(finding.action == DLPAction.BLOCK for finding in findings)
        risk_level = self._highest_risk(findings)

        for finding in sorted(findings, key=lambda item: item.start, reverse=True):
            replacement = self._replacement_for(finding)
            masked_text = masked_text[: finding.start] + replacement + masked_text[finding.end :]
            masked_entities.append(
                MaskedEntity(entity_type=finding.entity_type, masked_value=replacement)
            )

        return DLPResult(
            text=masked_text,
            findings=findings,
            masked_entities=list(reversed(masked_entities)),
            risk_level=risk_level,
            blocked=blocked,
        )

    def _replacement_for(self, finding: DLPFinding) -> str:
        if finding.replacement:
            return finding.replacement
        if finding.action == DLPAction.REDACT:
            return "[REDACTED]"
        if finding.action == DLPAction.BLOCK:
            return "[BLOCKED]"
        return f"[{finding.entity_type.upper()}]"

    def _highest_risk(self, findings: list[DLPFinding]) -> RiskLevel:
        rank = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
        return max((finding.risk_level for finding in findings), key=lambda level: rank.get(level, 0))
