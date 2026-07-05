from app.schemas.chat import Citation
from app.services.vector_store_service import RetrievedChunk


class CitationBuilder:
    def from_chunks(self, chunks: list[RetrievedChunk]) -> list[Citation]:
        citations: list[Citation] = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            citations.append(
                Citation(
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    title=metadata.get("title"),
                    page_start=metadata.get("page_start"),
                    page_end=metadata.get("page_end"),
                    section_title=metadata.get("section_title"),
                    score=chunk.final_score,
                )
            )
        return citations

