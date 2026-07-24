import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.core.config import settings
from app.services.pdf_parser_service import ParsedDocument, ParsedPage
from app.utils.text_utils import clean_db_text


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float | None = None


class OCRUnavailableError(RuntimeError):
    pass


class ImageOCRService:
    def is_available(self) -> bool:
        if self._rapidocr_available():
            return True
        if self._tesseract_cmd() is None:
            return False
        try:
            import PIL.Image  # noqa: F401
            import pypdfium2  # noqa: F401
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return True

    async def run_ocr(self, file_path: str) -> OCRResult:
        if self._rapidocr_available():
            return self._run_rapidocr(file_path)

        self._ensure_tesseract_runtime()
        import pytesseract
        from PIL import Image

        image = Image.open(file_path)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        return self._result_from_tesseract_data(data)

    async def run_pdf_ocr(self, file_path: str, scale: float = 2.0) -> ParsedDocument:
        if self._rapidocr_available():
            return await self._run_pdf_rapidocr(file_path, scale)

        self._ensure_tesseract_runtime()
        import pypdfium2 as pdfium
        import pytesseract

        pdf = pdfium.PdfDocument(file_path)
        pages: list[ParsedPage] = []
        confidences: list[float] = []

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for index in range(len(pdf)):
                page = pdf[index]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                image_path = temp_path / f"page-{index + 1}.png"
                image.save(image_path)
                data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                result = self._result_from_tesseract_data(data)
                if result.confidence is not None:
                    confidences.append(result.confidence)
                pages.append(
                    ParsedPage(
                        page_number=index + 1,
                        text=result.text.strip(),
                        layout_text=result.text.strip(),
                        ocr_confidence=result.confidence,
                    )
                )

        ocr_required = not any(page.text for page in pages)
        return ParsedDocument(
            pages=pages,
            page_count=len(pdf),
            ocr_required=ocr_required,
            ocr_confidence=(
                sum(confidences) / len(confidences) if confidences else None
            ),
        )

    async def _run_pdf_rapidocr(self, file_path: str, scale: float) -> ParsedDocument:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(file_path)
        pages: list[ParsedPage] = []
        confidences: list[float] = []

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for index in range(len(pdf)):
                page = pdf[index]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                image_path = temp_path / f"page-{index + 1}.png"
                image.save(image_path)
                result = self._run_rapidocr(image_path)
                if result.confidence is not None:
                    confidences.append(result.confidence)
                pages.append(
                    ParsedPage(
                        page_number=index + 1,
                        text=result.text.strip(),
                        layout_text=result.text.strip(),
                        ocr_confidence=result.confidence,
                    )
                )

        return ParsedDocument(
            pages=pages,
            page_count=len(pdf),
            ocr_required=not any(page.text for page in pages),
            ocr_confidence=sum(confidences) / len(confidences) if confidences else None,
        )

    def _run_rapidocr(self, image_path: str | Path) -> OCRResult:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        results, _elapsed = engine(str(image_path))
        if not results:
            return OCRResult(text="", confidence=None)

        items: list[tuple[float, float, str]] = []
        confidences: list[float] = []
        for item in results:
            if len(item) < 3:
                continue
            text = str(item[1]).strip()
            if not text:
                continue
            center_y, center_x = self._ocr_box_center(item[0])
            items.append((center_y, center_x, text))
            try:
                confidences.append(float(item[2]))
            except (TypeError, ValueError):
                continue

        return OCRResult(
            text=clean_db_text(self._join_positioned_text(items)) or "",
            confidence=sum(confidences) / len(confidences) if confidences else None,
        )

    def _rapidocr_available(self) -> bool:
        try:
            import cv2  # noqa: F401
            import onnxruntime  # noqa: F401
            import pypdfium2  # noqa: F401
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_tesseract_runtime(self) -> None:
        tesseract_cmd = self._tesseract_cmd()
        if tesseract_cmd is None:
            raise OCRUnavailableError(
                "No OCR runtime is available. RapidOCR dependencies are missing, and "
                "Tesseract OCR executable is not installed, not available on PATH, "
                "or TESSERACT_CMD is not configured."
            )
        missing: list[str] = []
        for module in ("PIL", "pypdfium2", "pytesseract"):
            try:
                __import__(module)
            except ImportError:
                missing.append(module)
        if missing:
            raise OCRUnavailableError(
                "OCR Python dependencies are missing: " + ", ".join(missing)
            )

        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def _tesseract_cmd(self) -> str | None:
        if settings.tesseract_cmd:
            configured = Path(settings.tesseract_cmd)
            if configured.exists():
                return str(configured)
        return shutil.which("tesseract")

    def _result_from_tesseract_data(self, data: dict) -> OCRResult:
        line_groups: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
        confidences: list[float] = []
        texts = data.get("text", [])
        confidences_raw = data.get("conf", [])
        block_nums = data.get("block_num", [])
        par_nums = data.get("par_num", [])
        line_nums = data.get("line_num", [])
        lefts = data.get("left", [])
        for index, (text, confidence) in enumerate(zip(texts, confidences_raw, strict=False)):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            key = (
                self._safe_int(block_nums, index),
                self._safe_int(par_nums, index),
                self._safe_int(line_nums, index),
            )
            line_groups.setdefault(key, []).append((self._safe_int(lefts, index), cleaned))
            try:
                numeric_confidence = float(confidence)
            except (TypeError, ValueError):
                continue
            if numeric_confidence >= 0:
                confidences.append(numeric_confidence / 100.0)

        lines = [
            " ".join(word for _left, word in sorted(words, key=lambda item: item[0]))
            for _key, words in sorted(line_groups.items(), key=lambda item: item[0])
        ]
        return OCRResult(
            text=clean_db_text("\n".join(lines)) or "",
            confidence=sum(confidences) / len(confidences) if confidences else None,
        )

    def _ocr_box_center(self, box: Any) -> tuple[float, float]:
        try:
            points = [(float(point[0]), float(point[1])) for point in box]
        except (TypeError, ValueError, IndexError):
            return 0.0, 0.0
        if not points:
            return 0.0, 0.0
        center_x = sum(point[0] for point in points) / len(points)
        center_y = sum(point[1] for point in points) / len(points)
        return center_y, center_x

    def _join_positioned_text(self, items: list[tuple[float, float, str]]) -> str:
        if not items:
            return ""
        sorted_items = sorted(items, key=lambda item: (item[0], item[1]))
        line_height = self._estimate_line_height(sorted_items)
        lines: list[list[tuple[float, str]]] = []
        current_y: float | None = None
        for center_y, center_x, text in sorted_items:
            if current_y is None or abs(center_y - current_y) > line_height:
                lines.append([])
                current_y = center_y
            lines[-1].append((center_x, text))
        return "\n".join(
            " ".join(text for _center_x, text in sorted(line, key=lambda item: item[0]))
            for line in lines
        )

    def _estimate_line_height(self, items: list[tuple[float, float, str]]) -> float:
        y_values = sorted({item[0] for item in items})
        gaps = [right - left for left, right in zip(y_values, y_values[1:], strict=False) if right > left]
        return max(8.0, (sum(gaps) / len(gaps) * 0.6) if gaps else 12.0)

    def _safe_int(self, values: list, index: int) -> int:
        try:
            return int(values[index])
        except (IndexError, TypeError, ValueError):
            return 0
