from pathlib import Path

from PIL import Image

import app.services.pdf_processing_service as pdf_processing_module
from app.services.pdf_markdown_conversion_service import convert_pdf_with_markitdown
from app.services.pdf_parser_service import ParsedDocument, ParsedPage
from app.services.pdf_processing_service import process_pdf_file
from app.workers.process_runner import run_in_process


class FakeMarkItDown:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def convert_local(self, path: Path):
        self.paths.append(path)
        return type("Result", (), {"text_content": "# Converted\r\n\r\nPDF body"})()


class ExtractTextMustNotRun:
    images: tuple = ()

    def extract_text(self, *args, **kwargs):
        raise AssertionError("pypdf text extraction must be skipped for forced OCR")


class FakePdfReader:
    def __init__(self, *args, **kwargs) -> None:
        self.pages = [ExtractTextMustNotRun()]


class FakeOCRService:
    def is_available(self) -> bool:
        return True

    def run_pdf_ocr_sync(self, file_path: str, *, max_pages: int):
        return ParsedDocument(
            pages=[ParsedPage(page_number=1, text="OCR result")],
            page_count=1,
            ocr_required=False,
            ocr_confidence=0.91,
        )


def test_ordinary_pdf_is_converted_directly_by_markitdown(tmp_path: Path) -> None:
    pdf_path = tmp_path / "ordinary.pdf"
    Image.new("RGB", (100, 80), "white").save(pdf_path, "PDF")
    converter = FakeMarkItDown()

    result = convert_pdf_with_markitdown(
        file_path=str(pdf_path),
        max_file_bytes=1_000_000,
        max_pages=5,
        converter=converter,
    )

    assert converter.paths == [pdf_path]
    assert result.page_count == 1
    assert result.markdown == "# Converted\n\nPDF body\n"


def test_real_markitdown_pdf_conversion_is_picklable_and_isolated(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    Image.new("RGB", (100, 80), "white").save(pdf_path, "PDF")

    result = run_in_process(
        convert_pdf_with_markitdown,
        timeout_seconds=10,
        kwargs={
            "file_path": str(pdf_path),
            "max_file_bytes": 1_000_000,
            "max_pages": 5,
        },
    )

    assert result.page_count == 1
    assert result.markdown == ""


def test_markitdown_empty_result_forces_existing_ocr_without_pypdf_text_extraction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake for patched reader")
    monkeypatch.setattr(pdf_processing_module, "PdfReader", FakePdfReader)
    monkeypatch.setattr(pdf_processing_module, "ImageOCRService", FakeOCRService)

    result = process_pdf_file(
        file_path=str(pdf_path),
        document_id="scanned-test",
        image_root=str(tmp_path / "images"),
        max_file_bytes=1_000_000,
        max_pages=5,
        page_timeout_seconds=5.0,
        enable_pdf_ocr=True,
        enable_image_extraction=False,
        image_max_pages=0,
        image_max_count=0,
        enable_image_ocr=False,
        image_ocr_max_count=0,
        force_pdf_ocr=True,
    )

    assert result.used_pdf_ocr is True
    assert result.parsed.text == "OCR result"
    assert result.parsed.ocr_confidence == 0.91
