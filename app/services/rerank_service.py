from app.services.vector_store_service import RetrievedChunk


class RerankService:
    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_n: int,
    ) -> list[RetrievedChunk]:
        return chunks[:top_n]

