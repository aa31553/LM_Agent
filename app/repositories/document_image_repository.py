from sqlalchemy import delete, or_, select

from app.models.document_image import DocumentImage
from app.repositories.base import BaseRepository


class DocumentImageRepository(BaseRepository[DocumentImage]):
    model = DocumentImage

    def add_many(self, images: list[DocumentImage]) -> list[DocumentImage]:
        self.db.add_all(images)
        return images

    def delete_by_document_id(self, document_id: object) -> int:
        result = self.db.execute(delete(DocumentImage).where(DocumentImage.document_id == document_id))
        return int(result.rowcount or 0)

    def list_by_ids(self, image_ids: list[object]) -> list[DocumentImage]:
        if not image_ids:
            return []
        return list(
            self.db.scalars(
                select(DocumentImage)
                .where(DocumentImage.id.in_(image_ids))
                .order_by(DocumentImage.page_number.asc(), DocumentImage.image_index.asc())
            )
        )

    def list_for_document_pages(
        self,
        document_pages: list[tuple[object, int]],
    ) -> list[DocumentImage]:
        if not document_pages:
            return []
        filters = [
            (DocumentImage.document_id == document_id) & (DocumentImage.page_number == page_number)
            for document_id, page_number in document_pages
        ]
        return (
            self.db.query(DocumentImage)
            .filter(or_(*filters))
            .order_by(DocumentImage.page_number.asc(), DocumentImage.image_index.asc())
            .all()
        )
