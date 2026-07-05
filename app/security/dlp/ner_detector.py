import re

from app.core.constants import DLPAction, RiskLevel
from app.security.dlp.base import DLPFinding


class NERDetector:
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("person_name", re.compile(r"\b(?:name|owner|contact)\s*[:=]\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b")),
        ("organization", re.compile(r"\b[A-Z][A-Za-z0-9&.-]+(?:\s+[A-Z][A-Za-z0-9&.-]+){0,3}\s+(?:Inc|Corp|Co|Ltd|LLC)\b")),
    )

    def detect(self, text: str) -> list[DLPFinding]:
        findings: list[DLPFinding] = []
        for entity_type, pattern in self.patterns:
            for match in pattern.finditer(text):
                findings.append(
                    DLPFinding(
                        entity_type=entity_type,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        action=DLPAction.MASK,
                        risk_level=RiskLevel.MEDIUM,
                        confidence=0.7,
                    )
                )
        return findings
