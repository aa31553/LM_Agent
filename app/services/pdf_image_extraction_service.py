from dataclasses import dataclass
from pathlib import Path
import re
from uuid import uuid4

from PIL import Image
from pypdf import PdfReader

from app.models.document import Document
from app.models.document_image import DocumentImage
from app.services.image_ocr_service import ImageOCRService, OCRUnavailableError
from app.services.pdf_parser_service import ParsedDocument, ParsedPage
from app.utils.text_utils import clean_db_text


@dataclass(frozen=True)
class ExtractedImage:
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


class PDFImageExtractionService:
    caption_pattern = re.compile(
        r"^\s*(?:圖|Fig\.?|Figure)\s*[\dA-Za-z一二三四五六七八九十IVXivx.\-:：]*\s+.+",
        re.IGNORECASE,
    )

    def __init__(
        self,
        image_root: str | Path = "data/extracted_images",
        ocr_service: ImageOCRService | None = None,
    ) -> None:
        self.image_root = Path(image_root)
        self.ocr_service = ocr_service or ImageOCRService()

    async def extract(self, document: Document, parsed: ParsedDocument) -> list[DocumentImage]:
        extracted = await self.extract_file(
            file_path=document.file_path,
            document_id=str(document.id),
            parsed=parsed,
        )
        return [
            DocumentImage(
                id=uuid4(),
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                page_number=item.page_number,
                image_index=item.image_index,
                caption=item.caption,
                image_path=item.image_path,
                mime_type=item.mime_type,
                width=item.width,
                height=item.height,
                ocr_text=clean_db_text(item.ocr_text),
                extraction_method=item.extraction_method,
                image_metadata=item.metadata,
            )
            for item in extracted
        ]

    async def extract_file(
        self,
        *,
        file_path: str,
        document_id: str,
        parsed: ParsedDocument,
    ) -> list[ExtractedImage]:
        reader = PdfReader(file_path)
        output_dir = self.image_root / document_id
        output_dir.mkdir(parents=True, exist_ok=True)
        parsed_pages = {page.page_number: page for page in parsed.pages}
        images: list[ExtractedImage] = []

        for page_index, page in enumerate(reader.pages, start=1):
            page_images = list(getattr(page, "images", []))
            captions = self._captions_for_page(parsed_pages.get(page_index))
            for image_index, image in enumerate(page_images, start=1):
                image_path = output_dir / self._image_filename(page_index, image_index, image)
                image_path.write_bytes(getattr(image, "data", b""))
                width, height, mime_type = self._inspect_image(image_path)
                ocr_text = await self._ocr_image(image_path)
                images.append(
                    ExtractedImage(
                        page_number=page_index,
                        image_index=image_index,
                        caption=clean_db_text(self._caption_for_image(captions, image_index)),
                        image_path=str(image_path),
                        mime_type=mime_type,
                        width=width,
                        height=height,
                        ocr_text=ocr_text,
                        extraction_method="embedded_image",
                        metadata={"source_name": getattr(image, "name", None)},
                    )
                )

            if not page_images and captions:
                fallback = await self._render_page_image(
                    file_path=file_path,
                    output_dir=output_dir,
                    page_number=page_index,
                    image_index=1,
                )
                if fallback is not None:
                    width, height, mime_type = self._inspect_image(fallback)
                    images.append(
                        ExtractedImage(
                            page_number=page_index,
                            image_index=1,
                            caption=clean_db_text(captions[0]),
                            image_path=str(fallback),
                            mime_type=mime_type,
                            width=width,
                            height=height,
                            ocr_text=clean_db_text(
                                parsed_pages.get(page_index).text if parsed_pages.get(page_index) else None
                            ),
                            extraction_method="page_render",
                            metadata={"fallback_reason": "caption_without_embedded_image"},
                        )
                    )
        return images

    def _captions_for_page(self, page: ParsedPage | None) -> list[str]:
        if page is None:
            return []
        lines = (page.layout_text or page.text).splitlines()
        captions = [line.strip() for line in lines if self.caption_pattern.match(line.strip())]
        return captions

    def _caption_for_image(self, captions: list[str], image_index: int) -> str | None:
        if not captions:
            return None
        if image_index <= len(captions):
            return captions[image_index - 1]
        return captions[-1]

    def _image_filename(self, page_number: int, image_index: int, image) -> str:
        name = str(getattr(image, "name", "") or "")
        suffix = Path(name).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
            suffix = ".png"
        return f"page-{page_number:04d}-image-{image_index:03d}{suffix}"

    def _inspect_image(self, image_path: Path) -> tuple[int | None, int | None, str | None]:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                mime_type = Image.MIME.get(image.format or "")
                return width, height, mime_type
        except OSError:
            return None, None, None

    async def _ocr_image(self, image_path: Path) -> str | None:
        if not self.ocr_service.is_available():
            return None
        try:
            result = await self.ocr_service.run_ocr(str(image_path))
        except (OCRUnavailableError, OSError, RuntimeError):
            return None
        return clean_db_text(result.text.strip()) or None

    async def _render_page_image(
        self,
        *,
        file_path: str,
        output_dir: Path,
        page_number: int,
        image_index: int,
    ) -> Path | None:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None
        pdf = pdfium.PdfDocument(file_path)
        if page_number < 1 or page_number > len(pdf):
            return None
        page = pdf[page_number - 1]
        bitmap = page.render(scale=2.0)
        image = bitmap.to_pil()
        output_path = output_dir / f"page-{page_number:04d}-image-{image_index:03d}.png"
        image.save(output_path)
        return output_path
