from dataclasses import dataclass, field

from app.core.constants import DLPAction, RiskLevel
from app.schemas.chat import MaskedEntity


@dataclass(frozen=True)
class DLPFinding:
    entity_type: str
    value: str
    start: int
    end: int
    action: DLPAction
    risk_level: RiskLevel
    confidence: float = 1.0
    replacement: str | None = None


@dataclass(frozen=True)
class DLPResult:
    text: str
    findings: list[DLPFinding] = field(default_factory=list)
    masked_entities: list[MaskedEntity] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    blocked: bool = False
