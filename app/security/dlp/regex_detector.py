import re

from app.core.constants import DLPAction, RiskLevel
from app.security.dlp.base import DLPFinding


class RegexDetector:
    patterns: tuple[tuple[str, re.Pattern[str], DLPAction, RiskLevel], ...] = (
        ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), DLPAction.MASK, RiskLevel.LOW),
        ("phone_number", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}\b"), DLPAction.MASK, RiskLevel.LOW),
        ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), DLPAction.MASK, RiskLevel.LOW),
        ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), DLPAction.REDACT, RiskLevel.HIGH),
        ("secret", re.compile(r"\b(?:password|token|secret)\s*[:=]\s*\S+", re.I), DLPAction.REDACT, RiskLevel.HIGH),
        ("url", re.compile(r"https?://[^\s]+", re.I), DLPAction.MASK, RiskLevel.LOW),
        ("file_path", re.compile(r"(?:[A-Za-z]:\\|/)[^\s]+"), DLPAction.MASK, RiskLevel.MEDIUM),
        ("lot_number", re.compile(r"\b(?:lot|batch)[-_:\s]*[A-Z0-9-]{4,}\b", re.I), DLPAction.MASK, RiskLevel.MEDIUM),
        ("machine_name", re.compile(r"\b(?:machine|tool|eqp)[-_:\s]*[A-Z0-9-]{3,}\b", re.I), DLPAction.MASK, RiskLevel.MEDIUM),
        ("process_parameter", re.compile(r"\b(?:temperature|temp|pressure|voltage|current|rpm)\s*[:=]\s*[-+]?\d+(?:\.\d+)?\s*[A-Za-z%/]*", re.I), DLPAction.MASK, RiskLevel.MEDIUM),
        ("customer_code", re.compile(r"\b(?:customer|cust)[-_:\s]*[A-Z0-9-]{3,}\b", re.I), DLPAction.MASK, RiskLevel.MEDIUM),
        ("product_code", re.compile(r"\b(?:product|prod|model)[-_:\s]*[A-Z0-9-]{3,}\b", re.I), DLPAction.MASK, RiskLevel.MEDIUM),
    )

    def detect(self, text: str) -> list[DLPFinding]:
        findings: list[DLPFinding] = []
        for entity_type, pattern, action, risk_level in self.patterns:
            for match in pattern.finditer(text):
                findings.append(
                    DLPFinding(
                        entity_type=entity_type,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        action=action,
                        risk_level=risk_level,
                    )
                )
        return sorted(findings, key=lambda finding: finding.start)
