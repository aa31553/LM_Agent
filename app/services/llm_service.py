from collections.abc import AsyncIterator
from typing import Any

from app.core.constants import ThinkingMode
from app.integrations.openai_compatible_client import ChatCompletionResult, OpenAICompatibleClient


class LLMService:
    def __init__(
        self,
        client: OpenAICompatibleClient | None = None,
        *,
        model: str | None = None,
        thinking_mode: ThinkingMode = ThinkingMode.DEFAULT,
    ) -> None:
        self.client = client or OpenAICompatibleClient(
            model=model,
            thinking_mode=thinking_mode,
        )

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

    def stream_complete_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        return self.client.stream_chat_completion_messages(messages=messages)
