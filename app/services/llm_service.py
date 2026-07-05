from collections.abc import AsyncIterator
from typing import Any

from app.integrations.openai_compatible_client import ChatCompletionResult
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

    async def complete_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        return await self.client.chat_completion_messages(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
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
