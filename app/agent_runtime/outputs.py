from typing import Literal

from pydantic import BaseModel, Field


class AnswerOutput(BaseModel):
    """Typed answer contract used when structured agent output is enabled."""

    answer: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high", "insufficient"] = "insufficient"
    limitations: list[str] = Field(default_factory=list)


def to_legacy_answer(output: AnswerOutput) -> str:
    """Render the typed result for the unchanged public Chat response contract."""

    sections = [f"### 1. Answer\n{output.answer}"]
    sections.append("### 2. Key points\n" + _bullets(output.key_points))
    sections.append("### 3. Sources\n" + _bullets(output.sources))
    sections.append(f"### 4. Confidence\n{output.confidence}")
    sections.append("### 5. Limitations\n" + _bullets(output.limitations))
    return "\n\n".join(sections)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None"
