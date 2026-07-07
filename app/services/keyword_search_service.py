import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import Principal
from app.services.vector_store_service import RetrievedChunk
from app.services.vector_store_service import VectorStoreService


class KeywordSearchService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    async def search(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        principal: Principal | None = None,
    ) -> list[RetrievedChunk]:
        if self.db is None or not knowledge_base_ids or not query.strip():
            return []

        permission_sql, permission_params = VectorStoreService(self.db)._permission_filter(principal)
        terms = self._query_terms(query)
        term_filters = " OR ".join(f"c.content ILIKE :term_{index}" for index, _ in enumerate(terms))
        match_sql = f"({term_filters})" if term_filters else "c.content ILIKE :like_query"
        term_score_sql = " + ".join(
            f"CASE WHEN c.content ILIKE :term_{index} THEN 0.08 ELSE 0 END"
            for index, _ in enumerate(terms)
        )
        keyword_score_sql = "similarity(c.content, :query)"
        if term_score_sql:
            keyword_score_sql = f"({keyword_score_sql} + {term_score_sql})"
        rows = self.db.execute(
            text(
                f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.content,
                    c.confidential_level,
                    c.source_type,
                    c.page_start,
                    c.page_end,
                    c.section_title,
                    c.metadata,
                    COALESCE(d.title, d.original_filename, d.filename) AS document_title,
                    {keyword_score_sql} AS keyword_score
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.knowledge_base_id = ANY(:knowledge_base_ids)
                  AND {match_sql}
                  AND d.status = 'ready'
                  {permission_sql}
                ORDER BY keyword_score DESC
                LIMIT :top_k
                """
            ),
            {
                "query": query,
                "like_query": f"%{query}%",
                **{f"term_{index}": f"%{term}%" for index, term in enumerate(terms)},
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "top_k": top_k,
                **permission_params,
            },
        ).mappings()

        return [
            RetrievedChunk(
                chunk_id=row["id"],
                document_id=row["document_id"],
                content=row["content"],
                vector_score=0.0,
                keyword_score=float(row["keyword_score"] or 0.0),
                final_score=float(row["keyword_score"] or 0.0),
                metadata={
                    **(row["metadata"] or {}),
                    "confidential_level": row["confidential_level"],
                    "source_type": row["source_type"],
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                    "section_title": row["section_title"],
                    "title": row["document_title"],
                },
            )
            for row in rows
        ]

    def _query_terms(self, query: str) -> list[str]:
        stopwords = {
            "about",
            "after",
            "and",
            "are",
            "for",
            "from",
            "how",
            "into",
            "the",
            "this",
            "with",
            "與",
            "和",
            "的",
            "是",
            "在",
            "對",
            "請",
            "說明",
        }
        terms: list[str] = []
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,8}", query):
            term = raw.strip()
            if term.lower() in stopwords or term in stopwords:
                continue
            if term not in terms:
                terms.append(term)
            if len(terms) >= 12:
                break
        return terms
