from dataclasses import dataclass
import re

from pypdf import PdfReader

from app.utils.text_utils import clean_db_text


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str
    layout_text: str | None = None
    tables: list[str] | None = None
    fields: dict[str, str] | None = None
    ocr_confidence: float | None = None


@dataclass(frozen=True)
class ParsedDocument:
    pages: list[ParsedPage]
    page_count: int
    ocr_required: bool = False
    ocr_confidence: float | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())


class PDFParserService:
    async def parse(self, file_path: str) -> ParsedDocument:
        reader = PdfReader(file_path)
        pages: list[ParsedPage] = []
        for index, page in enumerate(reader.pages, start=1):
            plain_text = page.extract_text(extraction_mode="plain") or ""
            try:
                layout_text = page.extract_text(extraction_mode="layout") or plain_text
            except TypeError:
                layout_text = plain_text
            normalized = self._normalize_text(layout_text or plain_text)
            pages.append(
                ParsedPage(
                    page_number=index,
                    text=self._compose_structured_text(normalized),
                    layout_text=normalized,
                    tables=self._extract_table_blocks(normalized),
                    fields=self._extract_fields(normalized),
                )
            )

        has_text = any(page.text for page in pages)
        return ParsedDocument(
            pages=pages,
            page_count=len(reader.pages),
            ocr_required=not has_text,
        )

    def _normalize_text(self, text: str) -> str:
        text = clean_db_text(text) or ""
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        cleaned: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if cleaned and cleaned[-1]:
                    cleaned.append("")
                continue
            if cleaned and cleaned[-1].endswith("-") and stripped[:1].islower():
                cleaned[-1] = cleaned[-1][:-1] + stripped
                continue
            cleaned.append(re.sub(r"[ \t]{3,}", "  ", stripped))
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        return "\n".join(cleaned)

    def _compose_structured_text(self, text: str) -> str:
        if not text.strip():
            return ""
        sections = [text.strip()]
        tables = self._extract_table_blocks(text)
        fields = self._extract_fields(text)
        if tables:
            sections.append("[Detected tables]\n" + "\n\n".join(tables))
        if fields:
            field_lines = [f"{key}: {value}" for key, value in fields.items()]
            sections.append("[Detected fields]\n" + "\n".join(field_lines))
        return "\n\n".join(sections)

    def _extract_table_blocks(self, text: str) -> list[str]:
        table_lines: list[str] = []
        blocks: list[str] = []
        for line in text.splitlines():
            if self._looks_like_table_line(line):
                table_lines.append(self._table_line_to_markdown(line))
            elif table_lines:
                if len(table_lines) >= 2:
                    blocks.append("\n".join(table_lines))
                table_lines = []
        if len(table_lines) >= 2:
            blocks.append("\n".join(table_lines))
        return blocks

    def _looks_like_table_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if "|" in stripped and stripped.count("|") >= 2:
            return True
        if re.search(r"\S\s{2,}\S", stripped):
            parts = [part for part in re.split(r"\s{2,}", stripped) if part]
            return len(parts) >= 3
        return False

    def _table_line_to_markdown(self, line: str) -> str:
        if "|" in line:
            cells = [cell.strip() for cell in line.strip("| ").split("|")]
        else:
            cells = [cell.strip() for cell in re.split(r"\s{2,}", line.strip()) if cell.strip()]
        return "| " + " | ".join(cells) + " |"

    def _extract_fields(self, text: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in text.splitlines():
            match = re.match(r"^([A-Za-z][A-Za-z0-9 _/\-]{1,40})\s*[:：]\s*(.{1,120})$", line.strip())
            if not match:
                continue
            key = re.sub(r"\s+", " ", match.group(1)).strip()
            value = match.group(2).strip()
            if key and value:
                fields[key] = value
        return fields
