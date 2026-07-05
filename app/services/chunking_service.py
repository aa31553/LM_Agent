from dataclasses import dataclass, field

from app.services.pdf_parser_service import ParsedPage
from app.models.document_image import DocumentImage
from app.utils.token_counter import count_tokens


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    content: str
    token_count: int
    metadata: dict = field(default_factory=dict)


class ChunkingService:
    def chunk_text(self, text: str, chunk_size: int = 700, overlap: int = 120) -> list[TextChunk]:
        words = text.split()
        chunks: list[TextChunk] = []
        start = 0
        index = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            content = " ".join(words[start:end])
            chunks.append(TextChunk(index, content, count_tokens(content)))
            if end == len(words):
                break
            start = max(end - overlap, start + 1)
            index += 1
        return chunks

    def chunk_pages(
        self,
        pages: list[ParsedPage],
        chunk_size: int = 700,
        overlap: int = 120,
        source_type: str = "pdf_text",
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        buffer: list[str] = []
        buffer_pages: list[int] = []

        for page in pages:
            words = page.text.split()
            for word in words:
                buffer.append(word)
                buffer_pages.append(page.page_number)
                if len(buffer) >= chunk_size:
                    chunks.append(self._build_page_chunk(len(chunks), buffer, buffer_pages, source_type))
                    keep = min(overlap, len(buffer))
                    buffer = buffer[-keep:] if keep else []
                    buffer_pages = buffer_pages[-keep:] if keep else []

        if buffer:
            chunks.append(self._build_page_chunk(len(chunks), buffer, buffer_pages, source_type))
        return chunks

    def chunk_images(
        self,
        images: list[DocumentImage],
        start_index: int = 0,
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for offset, image in enumerate(images):
            parts = [
                f"Image on page {image.page_number}.",
                f"Image path: {image.image_path}",
            ]
            if image.caption:
                parts.append(f"Caption: {image.caption}")
            if image.ocr_text:
                parts.append(f"Image OCR text: {image.ocr_text}")
            content = "\n".join(parts)
            chunks.append(
                TextChunk(
                    chunk_index=start_index + offset,
                    content=content,
                    token_count=count_tokens(content),
                    metadata={
                        "page_start": image.page_number,
                        "page_end": image.page_number,
                        "source_type": "pdf_image",
                        "image_ids": [str(image.id)],
                        "image_path": image.image_path,
                        "image_caption": image.caption,
                        "image_ocr_text": image.ocr_text,
                    },
                )
            )
        return chunks

    def chunk_image_ocr_text(
        self,
        text: str,
        image: DocumentImage,
        start_index: int = 0,
        chunk_size: int = 700,
        overlap: int = 120,
    ) -> list[TextChunk]:
        chunks = self.chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        return [
            TextChunk(
                chunk_index=start_index + index,
                content=chunk.content,
                token_count=chunk.token_count,
                metadata={
                    "page_start": image.page_number,
                    "page_end": image.page_number,
                    "source_type": "image_ocr",
                    "image_ids": [str(image.id)],
                    "image_path": image.image_path,
                    "image_caption": image.caption,
                    "image_ocr_text": text,
                },
            )
            for index, chunk in enumerate(chunks)
        ]

    def _build_page_chunk(
        self,
        chunk_index: int,
        words: list[str],
        page_numbers: list[int],
        source_type: str,
    ) -> TextChunk:
        content = " ".join(words)
        return TextChunk(
            chunk_index=chunk_index,
            content=content,
            token_count=count_tokens(content),
            metadata={
                "page_start": min(page_numbers) if page_numbers else None,
                "page_end": max(page_numbers) if page_numbers else None,
                "source_type": source_type,
            },
        )
