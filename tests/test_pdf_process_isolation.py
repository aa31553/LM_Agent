import time

import pytest
from PIL import Image

from app.services.pdf_processing_service import PDFProcessingLimitError, process_pdf_file
from app.workers.process_runner import ProcessWorkerTimeoutError, run_in_process


def _sleep_for_test(*, seconds: float) -> None:
    time.sleep(seconds)


def _process_kwargs(pdf_path, image_root) -> dict:
    return {
        "file_path": str(pdf_path),
        "document_id": "pdf-process-test",
        "image_root": str(image_root),
        "max_file_bytes": 1_000_000,
        "max_pages": 5,
        "page_timeout_seconds": 5.0,
        "enable_pdf_ocr": False,
        "enable_image_extraction": False,
        "image_max_pages": 0,
        "image_max_count": 0,
        "enable_image_ocr": False,
        "image_ocr_max_count": 0,
    }


def test_pdf_processing_uses_picklable_process_result(tmp_path) -> None:
    pdf_path = tmp_path / "single-page.pdf"
    Image.new("RGB", (120, 80), "white").save(pdf_path, "PDF")

    result = run_in_process(
        process_pdf_file,
        timeout_seconds=10,
        kwargs=_process_kwargs(pdf_path, tmp_path / "images"),
    )

    assert result.parsed.page_count == 1
    assert result.images == []
    assert result.used_pdf_ocr is False


def test_pdf_processing_rejects_page_limit(tmp_path) -> None:
    pdf_path = tmp_path / "single-page.pdf"
    Image.new("RGB", (120, 80), "white").save(pdf_path, "PDF")
    kwargs = _process_kwargs(pdf_path, tmp_path / "images")
    kwargs["max_pages"] = 0

    try:
        process_pdf_file(**kwargs)
    except PDFProcessingLimitError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("PDF page limit must be enforced")


def test_pdf_subprocess_timeout_terminates_stuck_work() -> None:
    with pytest.raises(ProcessWorkerTimeoutError):
        run_in_process(_sleep_for_test, timeout_seconds=0.05, kwargs={"seconds": 1.0})
