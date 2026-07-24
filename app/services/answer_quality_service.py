from dataclasses import dataclass, field


@dataclass(frozen=True)
class AnswerQualityScore:
    score: int
    max_score: int = 100
    passed: bool = False
    matched_terms: list[str] = field(default_factory=list)
    missing_terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class AnswerQualityService:
    def score_grounded_answer(
        self,
        *,
        answer: str,
        required_terms: list[str],
        forbidden_terms: list[str] | None = None,
        requires_sources_section: bool = True,
    ) -> AnswerQualityScore:
        normalized = answer.lower()
        matched = [term for term in required_terms if term.lower() in normalized]
        missing = [term for term in required_terms if term.lower() not in normalized]
        forbidden = [term for term in (forbidden_terms or []) if term.lower() in normalized]

        score = 0
        notes: list[str] = []
        if required_terms:
            score += round(70 * len(matched) / len(required_terms))
        else:
            score += 70
        if requires_sources_section and "source" in normalized:
            score += 15
            notes.append("Answer includes a source section or source wording.")
        elif requires_sources_section:
            notes.append("Answer does not clearly include source wording.")
        if "insufficient" not in normalized and "not enough information" not in normalized:
            score += 10
        if forbidden:
            score -= 20 * len(forbidden)
            notes.append(f"Forbidden terms appeared: {', '.join(forbidden)}")
        if missing:
            notes.append(f"Missing required terms: {', '.join(missing)}")

        score = max(0, min(100, score))
        return AnswerQualityScore(
            score=score,
            passed=score >= 85 and not missing and not forbidden,
            matched_terms=matched,
            missing_terms=missing,
            notes=notes,
        )
