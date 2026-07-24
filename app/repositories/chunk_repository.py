from app.models.document_chunk import DocumentChunk
from app.repositories.base import BaseRepository
from sqlalchemy import delete, select


class ChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk

    def add_many(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        self.db.add_all(chunks)
        return chunks

    def delete_by_document_id(self, document_id: object) -> int:
        result = self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        return int(result.rowcount or 0)

    def count_by_document_id(self, document_id: object) -> int:
        return len(
            list(
                self.db.scalars(
                    select(DocumentChunk.id).where(DocumentChunk.document_id == document_id)
                )
            )
        )
