from collections.abc import AsyncIterator

from app.integrations.openai_compatible_client import OpenAICompatibleClient


class LLMService:
    def __init__(self, client: OpenAICompatibleClient | None = None) -> None:
        self.client = client or OpenAICompatibleClient()

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str] | None = None,
    ) -> str:
        return await self.client.chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_paths=image_paths,
        )

    def stream_complete(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str] | None = None,
    ) -> AsyncIterator[str]:
        return self.client.stream_chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_paths=image_paths,
        )
