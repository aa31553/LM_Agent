from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.constants import CONFIDENTIALITY_RANK
from app.core.security import Principal


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    content: str
    vector_score: float = 0.0
    keyword_score: float = 0.0
    final_score: float = 0.0
    metadata: dict | None = None


class VectorStoreService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    async def vector_search(
        self,
        query_embedding: list[float],
        knowledge_base_ids: list[UUID],
        top_k: int,
        principal: Principal | None = None,
    ) -> list[RetrievedChunk]:
        if self.db is None or not knowledge_base_ids:
            return []

        permission_sql, permission_params = self._permission_filter(principal)
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
                    1 - (c.embedding <=> CAST(:query_embedding AS vector)) AS vector_score
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.knowledge_base_id = ANY(:knowledge_base_ids)
                  AND c.embedding IS NOT NULL
                  AND d.status = 'ready'
                  {permission_sql}
                ORDER BY embedding <=> CAST(:query_embedding AS vector)
                LIMIT :top_k
                """
            ),
            {
                "query_embedding": self._vector_literal(query_embedding),
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
                vector_score=float(row["vector_score"] or 0.0),
                keyword_score=0.0,
                final_score=float(row["vector_score"] or 0.0),
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

    def _vector_literal(self, embedding: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"

    def _permission_filter(self, principal: Principal | None) -> tuple[str, dict]:
        if principal is None or "admin" in principal.roles:
            return "", {}

        allowed_levels = [
            level.value
            for level, rank in CONFIDENTIALITY_RANK.items()
            if rank <= CONFIDENTIALITY_RANK[principal.clearance_level]
        ]
        return (
            """
              AND d.confidential_level = ANY(:allowed_confidential_levels)
              AND (
                    d.department = :principal_department
                    OR (
                        (
                            NOT EXISTS (
                                SELECT 1 FROM knowledge_base_permissions kbp
                                WHERE kbp.knowledge_base_id = d.knowledge_base_id
                            )
                            OR EXISTS (
                                SELECT 1 FROM knowledge_base_permissions kbp
                                WHERE kbp.knowledge_base_id = d.knowledge_base_id
                                  AND kbp.permission IN ('read', 'admin')
                                  AND (
                                      (kbp.subject_type = 'user' AND kbp.subject_value = :principal_user)
                                      OR (kbp.subject_type = 'department' AND kbp.subject_value = :principal_department)
                                      OR (kbp.subject_type = 'role' AND kbp.subject_value = ANY(:principal_roles))
                                  )
                            )
                        )
                        AND (
                            NOT EXISTS (
                                SELECT 1 FROM document_permissions dp
                                WHERE dp.document_id = d.id
                            )
                            OR EXISTS (
                                SELECT 1 FROM document_permissions dp
                                WHERE dp.document_id = d.id
                                  AND dp.permission IN ('read', 'admin')
                                  AND (
                                      (dp.subject_type = 'user' AND dp.subject_value = :principal_user)
                                      OR (dp.subject_type = 'department' AND dp.subject_value = :principal_department)
                                      OR (dp.subject_type = 'role' AND dp.subject_value = ANY(:principal_roles))
                                  )
                            )
                        )
                          )
              )
            """,
            {
                "allowed_confidential_levels": allowed_levels,
                "principal_department": principal.department,
                "principal_user": principal.external_user_id,
                "principal_roles": list(principal.roles),
            },
        )
