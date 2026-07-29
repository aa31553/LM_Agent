import math
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import DocumentStatus, ErrorCode, RetrievalScope
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.retriever import HybridRetriever
from app.services.permission_service import PermissionService
from app.services.vector_store_service import RetrievedChunk


class ScopedHybridRetriever(HybridRetriever):
    """Restrict session retrieval without changing the existing RAG service contract."""

    def __init__(
        self,
        *,
        db: Session,
        attachment_ids: list[UUID],
        retrieval_scope: RetrievalScope,
    ) -> None:
        super().__init__(db=db)
        self.attachment_ids = list(dict.fromkeys(attachment_ids))
        self.retrieval_scope = retrieval_scope

    def validate_request(
        self,
        *,
        session_id: UUID | None,
        principal: Principal | None,
    ) -> None:
        scope = self._effective_scope()
        if scope == RetrievalScope.ATTACHMENTS_ONLY and not self.attachment_ids:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "attachment_ids is required when retrieval_scope is attachments_only.",
                400,
            )
        if scope in {
            RetrievalScope.ATTACHMENTS_ONLY,
            RetrievalScope.SESSION_ATTACHMENTS,
            RetrievalScope.SESSION_AND_KNOWLEDGE_BASES,
        } and session_id is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "session_id is required when session attachments are used.",
                400,
            )
        if self.attachment_ids:
            self._validate_attachments(session_id=session_id, principal=principal)

    async def retrieve(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal | None = None,
        session_id: UUID | None = None,
    ) -> list[RetrievedChunk]:
        scope = self._effective_scope()
        include_knowledge_bases = scope in {
            RetrievalScope.KNOWLEDGE_BASES_ONLY,
            RetrievalScope.SESSION_AND_KNOWLEDGE_BASES,
        }
        include_session = scope in {
            RetrievalScope.ATTACHMENTS_ONLY,
            RetrievalScope.SESSION_ATTACHMENTS,
            RetrievalScope.SESSION_AND_KNOWLEDGE_BASES,
        }

        if scope == RetrievalScope.ATTACHMENTS_ONLY and not self.attachment_ids:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "attachment_ids is required when retrieval_scope is attachments_only.",
                400,
            )
        if include_session and session_id is None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "session_id is required when session attachments are used.",
                400,
            )

        selected_ids = (
            self._validate_attachments(session_id=session_id, principal=principal)
            if include_session
            else []
        )
        query_embedding = (await self.embedding_service.embed_texts([query]))[0]

        vector_results: list[RetrievedChunk] = []
        keyword_results: list[RetrievedChunk] = []
        if include_knowledge_bases and knowledge_base_ids:
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

        session_results = (
            self._session_chunks_scoped(
                query=query,
                query_embedding=query_embedding,
                session_id=session_id,
                document_ids=selected_ids,
                top_k=top_k,
            )
            if include_session
            else []
        )

        merged = self._merge(vector_results, keyword_results, session_results)
        ranked = sorted(merged, key=lambda chunk: chunk.final_score, reverse=True)
        if use_rerank:
            return await self.reranker.rerank(
                query,
                ranked,
                max(top_k, settings.rerank_top_n),
            )
        return ranked[:top_k]

    def _effective_scope(self) -> RetrievalScope:
        if self.retrieval_scope != RetrievalScope.AUTO:
            return self.retrieval_scope
        return (
            RetrievalScope.ATTACHMENTS_ONLY
            if self.attachment_ids
            else RetrievalScope.SESSION_AND_KNOWLEDGE_BASES
        )

    def _validate_attachments(
        self,
        *,
        session_id: UUID | None,
        principal: Principal | None,
    ) -> list[UUID]:
        if self.db is None or session_id is None:
            return []
        if not self.attachment_ids:
            return []
        documents = list(
            self.db.scalars(select(Document).where(Document.id.in_(self.attachment_ids)))
        )
        if len(documents) != len(self.attachment_ids):
            raise APIError(
                ErrorCode.DOCUMENT_NOT_FOUND,
                "One or more attachments were not found.",
                404,
            )
        permission_service = PermissionService(self.db)
        for document in documents:
            if document.session_id != session_id:
                raise APIError(
                    ErrorCode.PERMISSION_DENIED,
                    "An attachment does not belong to the requested chat session.",
                    403,
                )
            if principal is not None:
                permission_service.ensure_document_read(principal, document)
            if document.status != DocumentStatus.READY.value:
                raise APIError(
                    ErrorCode.ATTACHMENT_NOT_READY,
                    "One or more attachments are still being processed.",
                    409,
                    details={"document_id": str(document.id), "status": document.status},
                )
        return [document.id for document in documents]

    def _session_chunks_scoped(
        self,
        *,
        query: str,
        query_embedding: list[float],
        session_id: UUID | None,
        document_ids: list[UUID],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if self.db is None or session_id is None:
            return []
        statement = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.session_id == session_id,
                Document.status == DocumentStatus.READY.value,
            )
        )
        if document_ids:
            statement = statement.where(Document.id.in_(document_ids))
        rows = self.db.execute(statement.limit(max(200, top_k * 20))).all()
        terms = [
            term.lower()
            for term in re.findall(r"[\w\u4e00-\u9fff]+", query)
            if len(term) > 1
        ]
        results: list[RetrievedChunk] = []
        for chunk, document in rows:
            content_lower = chunk.content.lower()
            keyword_score = sum(1 for term in terms if term in content_lower) / max(
                len(terms),
                1,
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
                        "title": (
                            document.title
                            or document.original_filename
                            or document.filename
                        ),
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
        dot = sum(
            left * right
            for left, right in zip(query_embedding, values, strict=True)
        )
        left_norm = math.sqrt(sum(value * value for value in query_embedding))
        right_norm = math.sqrt(sum(value * value for value in values))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
