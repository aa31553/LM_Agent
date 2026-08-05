"""CPU-isolated OCR fallback and image extraction used by the document worker.

This module deliberately keeps its entry point at module scope so it can run with
the ``spawn`` multiprocessing start method used on Windows. The API process never
imports or executes this fallback work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from PIL import Image
from pypdf import PdfReader

from app.services.image_ocr_service import ImageOCRService, OCRUnavailableError
from app.services.pdf_parser_service import ParsedDocument, ParsedPage, PDFParserService
from app.utils.text_utils import clean_db_text


class PDFProcessingLimitError(RuntimeError):
    """The uploaded PDF exceeded a configured resource boundary."""


@dataclass(frozen=True)
class ProcessedPDFImage:
    page_number: int
    image_index: int
    caption: str | None
    image_path: str
    mime_type: str | None
    width: int | None
    height: int | None
    ocr_text: str | None
    extraction_method: str
    metadata: dict


@dataclass(frozen=True)
class PDFProcessingResult:
    parsed: ParsedDocument
    images: list[ProcessedPDFImage]
    used_pdf_ocr: bool


def process_pdf_file(
    *,
    file_path: str,
    document_id: str,
    image_root: str,
    max_file_bytes: int,
    max_pages: int,
    page_timeout_seconds: float,
    enable_pdf_ocr: bool,
    enable_image_extraction: bool,
    image_max_pages: int,
    image_max_count: int,
    enable_image_ocr: bool,
    image_ocr_max_count: int,
    force_pdf_ocr: bool = False,
) -> PDFProcessingResult:
    """Parse text and optional images in one CPU worker process.

    The ordinary PDF path uses MarkItDown. This compatibility path runs only when
    MarkItDown produced no text and the document therefore needs the existing OCR
    flow. The parent process supplies the hard whole-job timeout.
    """

    source = Path(file_path)
    size = source.stat().st_size
    if size > max_file_bytes:
        raise PDFProcessingLimitError(
            f"PDF is {size} bytes; limit is {max_file_bytes} bytes."
        )

    reader = PdfReader(str(source), strict=False)
    page_count = len(reader.pages)
    if page_count > max_pages:
        raise PDFProcessingLimitError(
            f"PDF has {page_count} pages; limit is {max_pages} pages."
        )

    parser = PDFParserService()
    pages: list[ParsedPage] = []
    images: list[ProcessedPDFImage] = []
    output_dir = Path(image_root) / document_id
    ocr_service = ImageOCRService()
    image_ocr_count = 0

    for page_number, page in enumerate(reader.pages, start=1):
        started_at = perf_counter()
        if force_pdf_ocr:
            layout_text = ""
        else:
            try:
                layout_text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                layout_text = page.extract_text() or ""
        elapsed = perf_counter() - started_at
        if elapsed > page_timeout_seconds:
            raise PDFProcessingLimitError(
                f"PDF page {page_number} parsing took {elapsed:.1f}s; "
                f"limit is {page_timeout_seconds:.1f}s."
            )

        normalized = parser._normalize_text(layout_text)
        parsed_page = ParsedPage(
            page_number=page_number,
            text=parser._compose_structured_text(normalized),
            layout_text=normalized,
            tables=parser._extract_table_blocks(normalized),
            fields=parser._extract_fields(normalized),
        )
        pages.append(parsed_page)

        if (
            not enable_image_extraction
            or image_max_pages == 0
            or page_number > image_max_pages
            or len(images) >= image_max_count
        ):
            continue

        page_images = list(getattr(page, "images", []))
        captions = _captions_for_page(parsed_page)
        for image_index, image in enumerate(page_images, start=1):
            if len(images) >= image_max_count:
                break
            output_dir.mkdir(parents=True, exist_ok=True)
            image_path = output_dir / _image_filename(page_number, image_index, image)
            image_path.write_bytes(getattr(image, "data", b""))
            width, height, mime_type = _inspect_image(image_path)
            ocr_text: str | None = None
            if enable_image_ocr and image_ocr_count < image_ocr_max_count and ocr_service.is_available():
                try:
                    ocr_text = clean_db_text(ocr_service.run_ocr_sync(str(image_path)).text.strip())
                except (OCRUnavailableError, OSError, RuntimeError):
                    ocr_text = None
                image_ocr_count += 1
            images.append(
                ProcessedPDFImage(
                    page_number=page_number,
                    image_index=image_index,
                    caption=clean_db_text(_caption_for_image(captions, image_index)),
                    image_path=str(image_path),
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    ocr_text=ocr_text,
                    extraction_method="embedded_image",
                    metadata={"source_name": getattr(image, "name", None)},
                )
            )

    parsed = ParsedDocument(
        pages=pages,
        page_count=page_count,
        ocr_required=not any(page.text for page in pages),
    )
    if not parsed.ocr_required:
        return PDFProcessingResult(parsed=parsed, images=images, used_pdf_ocr=False)
    if not enable_pdf_ocr:
        return PDFProcessingResult(parsed=parsed, images=images, used_pdf_ocr=False)

    ocr_parsed = ocr_service.run_pdf_ocr_sync(
        str(source),
        max_pages=max_pages,
    )
    return PDFProcessingResult(parsed=ocr_parsed, images=images, used_pdf_ocr=True)


def _captions_for_page(page: ParsedPage) -> list[str]:
    import re

    pattern = re.compile(
        r"^\s*(?:圖|Fig\.?|Figure)\s*[\dA-Za-z一二三四五六七八九十IVXivx.\-:：]*\s+.+",
        re.IGNORECASE,
    )
    return [line.strip() for line in (page.layout_text or page.text).splitlines() if pattern.match(line)]


def _caption_for_image(captions: list[str], image_index: int) -> str | None:
    if not captions:
        return None
    return captions[image_index - 1] if image_index <= len(captions) else captions[-1]


def _image_filename(page_number: int, image_index: int, image: object) -> str:
    suffix = Path(str(getattr(image, "name", "") or "")).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        suffix = ".png"
    return f"page-{page_number:04d}-image-{image_index:03d}{suffix}"


def _inspect_image(image_path: Path) -> tuple[int | None, int | None, str | None]:
    try:
        with Image.open(image_path) as image:
            return image.width, image.height, Image.MIME.get(image.format or "")
    except OSError:
        return None, None, None
