from typing import Generic, TypeVar

from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, item_id: object) -> ModelT | None:
        return self.db.get(self.model, item_id)

    def add(self, model: ModelT) -> ModelT:
        self.db.add(model)
        return model

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, model: ModelT) -> ModelT:
        self.db.refresh(model)
        return model
