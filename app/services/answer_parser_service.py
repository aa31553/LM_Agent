import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedAnswer:
    answer: str
    key_points: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    confidence: str | None = None
    limitations: list[str] = field(default_factory=list)


_HEADING_PATTERN = re.compile(
    r"^\s*(?P<marker>#{1,6}\s*|\*\*\s*|__\s*)"
    r"(?:(?P<number>[1-5])\s*[.)-]?\s*)?"
    r"(?P<title>"
    r"answer|key\s*points?|sources?|confidence|limitations?|"
    r"回答|答案|重點|關鍵重點|來源|資料來源|信心|信心水準|限制|侷限"
    r")"
    r"\s*(?::\s*(?P<inline>.*?))?\s*(?:\*\*|__)?\s*$",
    re.IGNORECASE,
)
_NUMBERED_HEADING_PATTERN = re.compile(
    r"^\s*(?P<number>[1-5])\s*[.)-]\s*"
    r"(?P<title>"
    r"answer|key\s*points?|sources?|confidence|limitations?|"
    r"回答|答案|重點|關鍵重點|來源|資料來源|信心|信心水準|限制|侷限"
    r")"
    r"\s*(?::\s*(?P<inline>.*?))?\s*$",
    re.IGNORECASE,
)
_LIST_PREFIX_PATTERN = re.compile(r"^\s*(?:[-+*]\s+|\d+[.)]\s+)(.*)$")

_SECTION_NAMES = {
    "answer": "answer",
    "key point": "key_points",
    "key points": "key_points",
    "source": "sources",
    "sources": "sources",
    "confidence": "confidence",
    "limitation": "limitations",
    "limitations": "limitations",
    "回答": "answer",
    "答案": "answer",
    "重點": "key_points",
    "關鍵重點": "key_points",
    "來源": "sources",
    "資料來源": "sources",
    "信心": "confidence",
    "信心水準": "confidence",
    "限制": "limitations",
    "侷限": "limitations",
}


def parse_structured_answer(text: str) -> ParsedAnswer:
    normalized = (text or "").strip()
    if not normalized:
        return ParsedAnswer(answer="")

    sections: dict[str, list[str]] = {
        "answer": [],
        "key_points": [],
        "sources": [],
        "confidence": [],
        "limitations": [],
    }
    current_section = "answer"
    structured_heading_found = False

    for line in normalized.splitlines():
        heading = _parse_heading(line)
        if heading is not None:
            current_section, inline = heading
            structured_heading_found = True
            if inline:
                sections[current_section].append(inline)
            continue
        sections[current_section].append(line)

    if not structured_heading_found:
        return ParsedAnswer(answer=normalized)

    return ParsedAnswer(
        answer=_clean_block(sections["answer"]),
        key_points=_clean_list(sections["key_points"]),
        sources=_clean_list(sections["sources"]),
        confidence=_clean_confidence(sections["confidence"]),
        limitations=_clean_list(sections["limitations"]),
    )


def _parse_heading(line: str) -> tuple[str, str] | None:
    match = _HEADING_PATTERN.match(line) or _NUMBERED_HEADING_PATTERN.match(line)
    if match is None:
        return None
    title = " ".join(match.group("title").lower().split())
    section_name = _SECTION_NAMES.get(title)
    if section_name is None:
        return None
    inline = (match.group("inline") or "").strip()
    return section_name, inline


def _clean_block(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def _clean_optional_block(lines: list[str]) -> str | None:
    value = _clean_block(lines)
    return value or None


def _clean_confidence(lines: list[str]) -> str | None:
    value = _clean_optional_block(lines)
    if value is None:
        return None
    match = _LIST_PREFIX_PATTERN.match(value)
    return match.group(1).strip() if match is not None else value


def _clean_list(lines: list[str]) -> list[str]:
    items: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            value = " ".join(paragraph).strip()
            if value:
                items.append(value)
            paragraph.clear()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        bullet = _LIST_PREFIX_PATTERN.match(line)
        if bullet is not None:
            flush_paragraph()
            value = bullet.group(1).strip()
            if value:
                items.append(value)
            continue
        paragraph.append(line)

    flush_paragraph()
    return items
