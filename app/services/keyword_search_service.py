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
        rows = self.db.execute(
            text(
                f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.content,
                    c.confidential_level,
                    c.metadata,
                    similarity(c.content, :query) AS keyword_score
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.knowledge_base_id = ANY(:knowledge_base_ids)
                  AND c.content ILIKE :like_query
                  AND d.status = 'ready'
                  {permission_sql}
                ORDER BY similarity(c.content, :query) DESC
                LIMIT :top_k
                """
            ),
            {
                "query": query,
                "like_query": f"%{query}%",
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
                },
            )
            for row in rows
        ]
