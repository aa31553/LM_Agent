from __future__ import annotations

import csv
import html
import io
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import yaml

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.utils.text_utils import clean_db_text


@dataclass(frozen=True)
class ParsedTextDocument:
    markdown: str
    text: str
    page_count: int = 1


class _HTMLTextExtractor(HTMLParser):
    block_tags = {
        "address", "article", "aside", "blockquote", "br", "div", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "li", "main", "nav", "p", "section", "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1
        elif tag in self.block_tags and self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


class TextDocumentParserService:
    supported_types = {
        "txt", "md", "markdown", "log", "csv", "json", "yaml", "yml", "html", "htm", "xml"
    }

    async def parse(
        self,
        file_path: str | Path,
        file_type: str | None = None,
    ) -> ParsedTextDocument:
        path = Path(file_path)
        detected_type = (file_type or path.suffix.lower().lstrip(".")).lower()
        if detected_type not in self.supported_types:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                f"Unsupported text document type: .{detected_type}",
                400,
            )

        decoded = self._decode(path.read_bytes())
        try:
            markdown, extracted_text = self._convert(decoded, detected_type, path.stem)
        except (csv.Error, json.JSONDecodeError, yaml.YAMLError, ElementTree.ParseError) as exc:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                f"The .{detected_type} document is malformed: {exc}",
                400,
            ) from exc

        markdown = clean_db_text(markdown.strip()) or ""
        extracted_text = clean_db_text(extracted_text.strip()) or ""
        if not extracted_text:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Text document did not contain extractable text.",
                400,
            )
        return ParsedTextDocument(markdown=f"{markdown}\n", text=extracted_text)

    def _decode(self, content: bytes) -> str:
        encodings = ["utf-8-sig", "cp950"]
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            encodings.insert(0, "utf-16")
        for encoding in encodings:
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            "Text encoding is unsupported. Use UTF-8, UTF-16, or Traditional Chinese CP950.",
            400,
        )

    def _convert(self, content: str, file_type: str, title: str) -> tuple[str, str]:
        if file_type in {"md", "markdown"}:
            return content, content
        if file_type in {"txt", "log"}:
            return self._plain_markdown(title, content), content
        if file_type == "csv":
            return self._csv_markdown(title, content)
        if file_type == "json":
            parsed = json.loads(content)
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
            return f"# {title}\n\n```json\n{pretty}\n```", pretty
        if file_type in {"yaml", "yml"}:
            parsed = yaml.safe_load(content)
            pretty = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False).strip()
            return f"# {title}\n\n```yaml\n{pretty}\n```", pretty
        if file_type in {"html", "htm"}:
            parser = _HTMLTextExtractor()
            parser.feed(content)
            text = html.unescape(parser.text())
            return self._plain_markdown(title, text), text

        root = ElementTree.fromstring(content)
        text = "\n".join(part.strip() for part in root.itertext() if part.strip())
        return self._plain_markdown(title, text), text

    def _plain_markdown(self, title: str, text: str) -> str:
        return f"# {title}\n\n{text.strip()}"

    def _csv_markdown(self, title: str, content: str) -> tuple[str, str]:
        rows = list(csv.reader(io.StringIO(content)))
        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if not rows:
            return "", ""
        width = max(len(row) for row in rows)
        normalized = [row + [""] * (width - len(row)) for row in rows]

        def cell(value: str) -> str:
            return value.replace("|", "\\|").replace("\n", "<br>").strip()

        header = normalized[0]
        body = normalized[1:]
        table = [
            "| " + " | ".join(cell(value) for value in header) + " |",
            "| " + " | ".join("---" for _ in header) + " |",
            *("| " + " | ".join(cell(value) for value in row) + " |" for row in body),
        ]
        plain = "\n".join("\t".join(row) for row in normalized)
        return "\n".join([f"# {title}", "", *table]), plain
