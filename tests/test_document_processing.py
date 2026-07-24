import pytest
from PIL import Image, ImageDraw

from app.services.image_ocr_service import ImageOCRService, OCRUnavailableError
from app.services.chunking_service import ChunkingService
from app.services.pdf_image_extraction_service import PDFImageExtractionService
from app.services.pdf_parser_service import PDFParserService, ParsedDocument, ParsedPage
from app.services.vector_store_service import VectorStoreService
from app.utils.text_utils import clean_db_text


class FakeImageOCRService:
    def is_available(self) -> bool:
        return True

    async def run_ocr(self, file_path: str):
        return type("OCRResult", (), {"text": "axis: withdrawal rate", "confidence": 0.9})()


@pytest.mark.asyncio
async def test_data_pdf_is_detected_as_ocr_required() -> None:
    parsed = await PDFParserService().parse(
        "DATA/Guytons-Guardrails-Maximum-Inisital-Withdrawal-Rates.pdf"
    )

    assert parsed.page_count == 9
    assert parsed.ocr_required is True
    assert parsed.text == ""


def test_chunk_pages_preserves_page_metadata() -> None:
    pages = [
        ParsedPage(page_number=1, text="alpha beta gamma"),
        ParsedPage(page_number=2, text="delta epsilon zeta"),
    ]

    chunks = ChunkingService().chunk_pages(pages, chunk_size=4, overlap=1)

    assert len(chunks) == 2
    assert chunks[0].metadata["page_start"] == 1
    assert chunks[0].metadata["page_end"] == 2
    assert chunks[1].metadata["page_start"] == 2
    assert chunks[1].metadata["source_type"] == "pdf_text"


def test_ocr_result_preserves_line_layout() -> None:
    data = {
        "text": ["alpha", "beta", "gamma", "delta"],
        "conf": ["90", "91", "88", "87"],
        "block_num": [1, 1, 1, 1],
        "par_num": [1, 1, 1, 1],
        "line_num": [1, 1, 2, 2],
        "left": [10, 60, 10, 70],
    }

    result = ImageOCRService()._result_from_tesseract_data(data)

    assert result.text == "alpha beta\ngamma delta"
    assert result.confidence is not None
    assert result.confidence > 0.8


def test_clean_db_text_removes_nul_bytes() -> None:
    assert clean_db_text("alpha\x00beta\nok") == "alphabeta\nok"


def test_pdf_parser_structures_tables_and_fields() -> None:
    parser = PDFParserService()
    structured = parser._compose_structured_text(
        "Experiment: Withdrawal guardrails\n"
        "Metric  Baseline  Stress\n"
        "Rate  4%  3%\n"
        "Success  92%  88%"
    )

    assert "[Detected tables]" in structured
    assert "| Metric | Baseline | Stress |" in structured
    assert "[Detected fields]" in structured
    assert "Experiment: Withdrawal guardrails" in structured


@pytest.mark.asyncio
async def test_pdf_ocr_reports_missing_runtime() -> None:
    service = ImageOCRService()
    if service.is_available():
        pytest.skip("OCR runtime is installed on this machine.")

    with pytest.raises(OCRUnavailableError):
        await service.run_pdf_ocr("DATA/Guytons-Guardrails-Maximum-Inisital-Withdrawal-Rates.pdf")


def test_vector_literal_format() -> None:
    assert VectorStoreService()._vector_literal([0.1, 0.2]) == "[0.10000000,0.20000000]"


@pytest.mark.asyncio
async def test_pdf_image_extraction_saves_images_with_caption_and_ocr(tmp_path) -> None:
    image = Image.new("RGB", (220, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 20, 190, 80), outline="black", width=3)
    pdf_path = tmp_path / "figure.pdf"
    image.save(pdf_path, "PDF")

    parsed = ParsedDocument(
        pages=[
            ParsedPage(
                page_number=1,
                text="Figure 1 Withdrawal rate sensitivity chart",
                layout_text="Figure 1 Withdrawal rate sensitivity chart",
            )
        ],
        page_count=1,
    )
    extracted = await PDFImageExtractionService(
        image_root=tmp_path / "images",
        ocr_service=FakeImageOCRService(),
    ).extract_file(
        file_path=str(pdf_path),
        document_id="doc-test",
        parsed=parsed,
    )

    assert len(extracted) == 1
    assert extracted[0].caption == "Figure 1 Withdrawal rate sensitivity chart"
    assert extracted[0].ocr_text == "axis: withdrawal rate"
    assert extracted[0].width is not None
    assert extracted[0].height is not None
    assert (tmp_path / "images" / "doc-test").exists()
