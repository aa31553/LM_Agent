from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import Principal
from app.services.embedding_service import EmbeddingService
from app.services.keyword_search_service import KeywordSearchService
from app.services.rerank_service import RerankService
from app.services.vector_store_service import RetrievedChunk, VectorStoreService


class HybridRetriever:
    def __init__(self, db: Session | None = None) -> None:
        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStoreService(db=db)
        self.keyword_search = KeywordSearchService(db=db)
        self.reranker = RerankService()

    async def retrieve(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal | None = None,
    ) -> list[RetrievedChunk]:
        query_embedding = (await self.embedding_service.embed_texts([query]))[0]
        vector_results = await self.vector_store.vector_search(
            query_embedding,
            knowledge_base_ids,
            top_k,
            principal=principal,
        )
        keyword_results = await self.keyword_search.search(
            query,
            knowledge_base_ids,
            top_k,
            principal=principal,
        )
        merged = self._merge(vector_results, keyword_results)
        ranked = sorted(merged, key=lambda chunk: chunk.final_score, reverse=True)
        if use_rerank:
            return await self.reranker.rerank(query, ranked, max(top_k, settings.rerank_top_n))
        return ranked[:top_k]

    def _merge(
        self,
        vector_results: list[RetrievedChunk],
        keyword_results: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        by_id: dict[UUID, RetrievedChunk] = {chunk.chunk_id: chunk for chunk in vector_results}
        for keyword_chunk in keyword_results:
            existing = by_id.get(keyword_chunk.chunk_id)
            if existing is None:
                by_id[keyword_chunk.chunk_id] = keyword_chunk
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                content=chunk.content,
                vector_score=chunk.vector_score,
                keyword_score=chunk.keyword_score,
                final_score=(
                    chunk.vector_score * settings.vector_score_weight
                    + chunk.keyword_score * settings.keyword_score_weight
                ),
                metadata=chunk.metadata,
            )
            for chunk in by_id.values()
        ]
