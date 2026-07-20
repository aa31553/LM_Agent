import asyncio
from pathlib import Path
from typing import Protocol

from markitdown import MarkItDown


class MarkdownConverter(Protocol):
    def convert_local(self, path: str | Path):
        ...


class MarkdownConversionService:
    """Convert server-owned local artifacts to Markdown using MarkItDown."""

    def __init__(self, converter: MarkdownConverter | None = None) -> None:
        self.converter = converter or MarkItDown(enable_plugins=False)

    async def convert_local(self, file_path: str | Path) -> str:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Document artifact does not exist: {path}")

        result = await asyncio.to_thread(self.converter.convert_local, path)
        text = getattr(result, "text_content", None)
        if text is None:
            text = getattr(result, "markdown", None)
        if not isinstance(text, str):
            raise TypeError("MarkItDown returned a result without Markdown text.")
        return self._normalize(text)

    def from_extracted_text(self, title: str | None, text: str) -> str:
        body = self._normalize(text)
        heading = (title or "Document").strip()
        if not body:
            return ""
        return f"# {heading}\n\n{body}\n"

    def combine_image_ocr(self, title: str | None, ocr_text: str, converted: str) -> str:
        sections = [self.from_extracted_text(title, ocr_text).rstrip()]
        converted = self._normalize(converted)
        if converted and ocr_text.strip() not in converted:
            sections.extend(["## MarkItDown image metadata", converted])
        return "\n\n".join(section for section in sections if section).rstrip() + "\n"

    def _normalize(self, text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return f"{normalized}\n" if normalized else ""
