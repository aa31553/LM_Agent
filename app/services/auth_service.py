from app.core.security import Principal


class AuthService:
    async def validate_token(self, token: str) -> Principal:
        return Principal(external_user_id=token, username=token)

