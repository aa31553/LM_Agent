from app.models.permission import DocumentPermission, KnowledgeBasePermission
from app.repositories.base import BaseRepository
from sqlalchemy import delete, select


class DocumentPermissionRepository(BaseRepository[DocumentPermission]):
    model = DocumentPermission

    def list_for_document(self, document_id: object) -> list[DocumentPermission]:
        return list(
            self.db.scalars(
                select(DocumentPermission).where(DocumentPermission.document_id == document_id)
            )
        )

    def find_existing(
        self,
        document_id: object,
        subject_type: str,
        subject_value: str,
    ) -> DocumentPermission | None:
        return self.db.scalar(
            select(DocumentPermission).where(
                DocumentPermission.document_id == document_id,
                DocumentPermission.subject_type == subject_type,
                DocumentPermission.subject_value == subject_value,
            )
        )

    def delete_permission(self, permission_id: object) -> int:
        result = self.db.execute(
            delete(DocumentPermission).where(DocumentPermission.id == permission_id)
        )
        return int(result.rowcount or 0)


class KnowledgeBasePermissionRepository(BaseRepository[KnowledgeBasePermission]):
    model = KnowledgeBasePermission

    def list_for_knowledge_base(self, knowledge_base_id: object) -> list[KnowledgeBasePermission]:
        return list(
            self.db.scalars(
                select(KnowledgeBasePermission).where(
                    KnowledgeBasePermission.knowledge_base_id == knowledge_base_id
                )
            )
        )

    def find_existing(
        self,
        knowledge_base_id: object,
        subject_type: str,
        subject_value: str,
    ) -> KnowledgeBasePermission | None:
        return self.db.scalar(
            select(KnowledgeBasePermission).where(
                KnowledgeBasePermission.knowledge_base_id == knowledge_base_id,
                KnowledgeBasePermission.subject_type == subject_type,
                KnowledgeBasePermission.subject_value == subject_value,
            )
        )

    def delete_permission(self, permission_id: object) -> int:
        result = self.db.execute(
            delete(KnowledgeBasePermission).where(KnowledgeBasePermission.id == permission_id)
        )
        return int(result.rowcount or 0)
