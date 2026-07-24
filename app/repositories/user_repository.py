from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def get_by_external_user_id(self, external_user_id: str) -> User | None:
        return (
            self.db.query(User)
            .filter(User.external_user_id == external_user_id)
            .one_or_none()
        )
