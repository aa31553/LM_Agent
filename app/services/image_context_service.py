from uuid import UUID

from sqlalchemy.orm import Session

from app.models.document_image import DocumentImage
from app.repositories.document_image_repository import DocumentImageRepository
from app.schemas.chat import ImageReference
from app.services.vector_store_service import RetrievedChunk


class ImageContextService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def images_for_chunks(self, chunks: list[RetrievedChunk]) -> list[DocumentImage]:
        if self.db is None or not chunks:
            return []
        repository = DocumentImageRepository(self.db)
        explicit_ids = self._image_ids_from_chunks(chunks)
        images_by_id: dict[UUID, DocumentImage] = {
            image.id: image for image in repository.list_by_ids(explicit_ids)
        }

        document_pages = self._document_pages_from_chunks(chunks)
        for image in repository.list_for_document_pages(document_pages):
            images_by_id.setdefault(image.id, image)

        return sorted(
            images_by_id.values(),
            key=lambda item: (str(item.document_id), item.page_number, item.image_index),
        )

    def build_context(
        self,
        images: list[DocumentImage],
        max_images: int = 4,
        max_chars: int = 6000,
    ) -> str:
        if not images:
            return ""
        sections: list[str] = []
        used_chars = 0
        for index, image in enumerate(images[:max_images], start=1):
            lines = [
                f"[Image {index}]",
                f"Image ID: {image.id}",
                f"Document ID: {image.document_id}",
                f"Page: {image.page_number}",
                f"Path: {image.image_path}",
            ]
            if image.caption:
                lines.append(f"Caption: {image.caption}")
            if image.ocr_text:
                lines.append(f"OCR text: {image.ocr_text}")
            section = "\n".join(lines)
            remaining_chars = max_chars - used_chars
            if remaining_chars <= 0:
                break
            if len(section) > remaining_chars:
                section = section[:remaining_chars].rstrip()
            sections.append(section)
            used_chars += len(section) + 2
        return "\n\n".join(sections)

    def to_references(self, images: list[DocumentImage]) -> list[ImageReference]:
        return [
            ImageReference(
                image_id=image.id,
                document_id=image.document_id,
                page_number=image.page_number,
                caption=image.caption,
                image_path=image.image_path,
                ocr_text=image.ocr_text,
            )
            for image in images
        ]

    def _image_ids_from_chunks(self, chunks: list[RetrievedChunk]) -> list[UUID]:
        image_ids: list[UUID] = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            for raw_id in metadata.get("image_ids") or []:
                try:
                    image_ids.append(UUID(str(raw_id)))
                except ValueError:
                    continue
        return image_ids

    def _document_pages_from_chunks(self, chunks: list[RetrievedChunk]) -> list[tuple[UUID, int]]:
        document_pages: set[tuple[UUID, int]] = set()
        for chunk in chunks:
            metadata = chunk.metadata or {}
            page_start = metadata.get("page_start")
            page_end = metadata.get("page_end", page_start)
            if page_start is None:
                continue
            try:
                start = int(page_start)
                end = int(page_end)
            except (TypeError, ValueError):
                continue
            for page_number in range(start, end + 1):
                document_pages.add((chunk.document_id, page_number))
        return sorted(document_pages, key=lambda item: (str(item[0]), item[1]))
