import math
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import Principal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.embedding_service import EmbeddingService
from app.services.keyword_search_service import KeywordSearchService
from app.services.rerank_service import RerankService
from app.services.vector_store_service import RetrievedChunk, VectorStoreService


class HybridRetriever:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db
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
        session_id: UUID | None = None,
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
        session_results = self._session_chunks(
            query=query,
            query_embedding=query_embedding,
            session_id=session_id,
            top_k=top_k,
        )
        merged = self._merge(vector_results, keyword_results, session_results)
        ranked = sorted(merged, key=lambda chunk: chunk.final_score, reverse=True)
        if use_rerank:
            return await self.reranker.rerank(query, ranked, max(top_k, settings.rerank_top_n))
        return ranked[:top_k]

    def _merge(
        self,
        vector_results: list[RetrievedChunk],
        keyword_results: list[RetrievedChunk],
        session_results: list[RetrievedChunk] | None = None,
    ) -> list[RetrievedChunk]:
        by_id: dict[UUID, RetrievedChunk] = {chunk.chunk_id: chunk for chunk in vector_results}
        for keyword_chunk in keyword_results:
            existing = by_id.get(keyword_chunk.chunk_id)
            if existing is None:
                by_id[keyword_chunk.chunk_id] = keyword_chunk
        for session_chunk in session_results or []:
            by_id[session_chunk.chunk_id] = session_chunk
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

    def _session_chunks(
        self,
        *,
        query: str,
        query_embedding: list[float],
        session_id: UUID | None,
        top_k: int,
    ) -> list[RetrievedChunk]:
        if self.db is None or session_id is None:
            return []
        rows = self.db.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.session_id == session_id, Document.status == "ready")
            .limit(max(200, top_k * 20))
        ).all()
        terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1]
        results: list[RetrievedChunk] = []
        for chunk, document in rows:
            content_lower = chunk.content.lower()
            keyword_score = (
                sum(1 for term in terms if term in content_lower) / max(len(terms), 1)
            )
            vector_score = self._cosine_similarity(query_embedding, chunk.embedding)
            final_score = (
                vector_score * settings.vector_score_weight
                + keyword_score * settings.keyword_score_weight
            )
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    content=chunk.content,
                    vector_score=vector_score,
                    keyword_score=keyword_score,
                    final_score=final_score,
                    metadata={
                        **(chunk.chunk_metadata or {}),
                        "confidential_level": chunk.confidential_level,
                        "source_type": chunk.source_type,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "section_title": chunk.section_title,
                        "title": document.title or document.original_filename or document.filename,
                        "scope": "session",
                    },
                )
            )
        return sorted(results, key=lambda item: item.final_score, reverse=True)[:top_k]

    def _cosine_similarity(
        self,
        query_embedding: list[float],
        chunk_embedding: list[float] | None,
    ) -> float:
        if chunk_embedding is None:
            return 0.0
        values = [float(value) for value in chunk_embedding]
        if len(values) != len(query_embedding):
            return 0.0
        dot = sum(left * right for left, right in zip(query_embedding, values, strict=True))
        left_norm = math.sqrt(sum(value * value for value in query_embedding))
        right_norm = math.sqrt(sum(value * value for value in values))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
