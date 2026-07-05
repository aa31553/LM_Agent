import httpx
import pytest

from app.core.config import settings
from app.integrations.embedding_client import EmbeddingClient
from app.integrations.openai_compatible_client import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_embedding_client_uses_local_openai_compatible_endpoint() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"object": "embedding", "index": 1, "embedding": [0.4, 0.5, 0.6]},
                ],
                "model": settings.embedding_model,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        vectors = await EmbeddingClient(http_client=http_client).embed(["hello", "world"])

    assert captured["url"] == "http://127.0.0.1:1234/v1/embeddings"
    assert b"text-embedding-mxbai-embed-large-v1" in captured["payload"]
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


@pytest.mark.asyncio
async def test_llm_client_uses_gemma_chat_completion_model() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "測試回答"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        answer = await OpenAICompatibleClient(http_client=http_client).chat_completion(
            system_prompt="system",
            user_prompt="user",
        )

    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert b"google/gemma-4-12b-qat" in captured["payload"]
    assert b"reasoning_effort" in captured["payload"]
    assert b"none" in captured["payload"]
    assert b"system" in captured["payload"]
    assert b"user" in captured["payload"]
    assert answer == "測試回答"
