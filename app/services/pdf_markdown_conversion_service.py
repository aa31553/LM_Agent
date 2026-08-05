"""Killable MarkItDown conversion for ordinary text-based PDFs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pypdfium2 as pdfium
from markitdown import MarkItDown


class LocalMarkdownConverter(Protocol):
    def convert_local(self, path: Path): ...


class PDFMarkdownLimitError(RuntimeError):
    """The uploaded PDF exceeded a configured conversion boundary."""


@dataclass(frozen=True)
class PDFMarkdownResult:
    markdown: str
    page_count: int


def convert_pdf_with_markitdown(
    *,
    file_path: str,
    max_file_bytes: int,
    max_pages: int,
    converter: LocalMarkdownConverter | None = None,
) -> PDFMarkdownResult:
    """Convert a PDF without using pypdf for text extraction or page inspection."""

    source = Path(file_path)
    size = source.stat().st_size
    if size > max_file_bytes:
        raise PDFMarkdownLimitError(
            f"PDF is {size} bytes; limit is {max_file_bytes} bytes."
        )

    pdf = pdfium.PdfDocument(str(source))
    try:
        page_count = len(pdf)
    finally:
        pdf.close()
    if page_count > max_pages:
        raise PDFMarkdownLimitError(
            f"PDF has {page_count} pages; limit is {max_pages} pages."
        )

    markdown_converter = converter or MarkItDown(enable_plugins=False)
    result = markdown_converter.convert_local(source)
    text = getattr(result, "text_content", None)
    if text is None:
        text = getattr(result, "markdown", None)
    if not isinstance(text, str):
        raise TypeError("MarkItDown returned a result without Markdown text.")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return PDFMarkdownResult(
        markdown=f"{normalized}\n" if normalized else "",
        page_count=page_count,
    )
