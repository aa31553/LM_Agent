from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def list(
        self,
        knowledge_base_id: UUID | None = None,
        status: str | None = None,
        confidential_level: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Document]:
        statement = self._filtered_statement(
            knowledge_base_id=knowledge_base_id,
            status=status,
            confidential_level=confidential_level,
        )
        return list(
            self.db.scalars(
                statement.order_by(
                    (Document.status == "ready").desc(),
                    Document.created_at.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )

    def count(
        self,
        knowledge_base_id: UUID | None = None,
        status: str | None = None,
        confidential_level: str | None = None,
    ) -> int:
        statement = self._filtered_statement(
            knowledge_base_id=knowledge_base_id,
            status=status,
            confidential_level=confidential_level,
        )
        return int(self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0)

    def list_all(
        self,
        knowledge_base_id: UUID | None = None,
        status: str | None = None,
        confidential_level: str | None = None,
    ) -> list[Document]:
        statement = self._filtered_statement(
            knowledge_base_id=knowledge_base_id,
            status=status,
            confidential_level=confidential_level,
        )
        return list(
            self.db.scalars(
                statement.order_by((Document.status == "ready").desc(), Document.created_at.desc())
            )
        )

    def _filtered_statement(
        self,
        knowledge_base_id: UUID | None = None,
        status: str | None = None,
        confidential_level: str | None = None,
    ):
        statement = select(Document)
        if knowledge_base_id is not None:
            statement = statement.where(Document.knowledge_base_id == knowledge_base_id)
        if status is not None:
            statement = statement.where(Document.status == status)
        if confidential_level is not None:
            statement = statement.where(Document.confidential_level == confidential_level)
        return statement
