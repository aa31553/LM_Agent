from sqlalchemy import select

from app.models.masking import SensitiveDictionary
from app.repositories.base import BaseRepository


class SensitiveDictionaryRepository(BaseRepository[SensitiveDictionary]):
    model = SensitiveDictionary

    def list_active(self) -> list[SensitiveDictionary]:
        return list(
            self.db.scalars(
                select(SensitiveDictionary)
                .where(SensitiveDictionary.is_active.is_(True))
                .order_by(SensitiveDictionary.created_at.desc())
            )
        )

    def list_all(self) -> list[SensitiveDictionary]:
        return list(
            self.db.scalars(
                select(SensitiveDictionary).order_by(SensitiveDictionary.created_at.desc())
            )
        )

    def find_existing(self, entity_type: str, value: str) -> SensitiveDictionary | None:
        return self.db.scalar(
            select(SensitiveDictionary).where(
                SensitiveDictionary.entity_type == entity_type,
                SensitiveDictionary.value == value,
            )
        )
