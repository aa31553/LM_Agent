from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.security.dlp.base import DLPFinding, DLPResult
from app.security.dlp.dictionary_detector import DictionaryDetector
from app.security.dlp.masking_policy import MaskingPolicy
from app.security.dlp.ner_detector import NERDetector
from app.security.dlp.regex_detector import RegexDetector


class MaskingService:
    def __init__(self, db: Session | None = None) -> None:
        self.detectors = (RegexDetector(), DictionaryDetector(db), NERDetector())
        self.policy = MaskingPolicy()

    def scan_and_mask(self, text: str, location: str) -> DLPResult:
        findings = self._dedupe_findings(
            finding
            for detector in self.detectors
            for finding in detector.detect(text)
        )
        return self.policy.apply(text, findings, location=location)

    def _dedupe_findings(self, findings: Iterable[DLPFinding]) -> list[DLPFinding]:
        accepted: list[DLPFinding] = []
        occupied: set[int] = set()
        sorted_findings = sorted(findings, key=lambda item: (item.start, -(item.end - item.start)))
        for finding in sorted_findings:
            span = set(range(finding.start, finding.end))
            if occupied.intersection(span):
                continue
            accepted.append(finding)
            occupied.update(span)
        return accepted
