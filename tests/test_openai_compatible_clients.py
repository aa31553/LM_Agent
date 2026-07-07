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
        first = [0.1] * settings.embedding_dimension
        second = [0.2] * settings.embedding_dimension
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": first},
                    {"object": "embedding", "index": 1, "embedding": second},
                ],
                "model": settings.embedding_model,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        vectors = await EmbeddingClient(http_client=http_client).embed(["hello", "world"])

    assert captured["url"] == "http://127.0.0.1:1234/v1/embeddings"
    assert b"text-embedding-mxbai-embed-large-v1" in captured["payload"]
    assert len(vectors) == 2
    assert len(vectors[0]) == settings.embedding_dimension
    assert vectors[0][:3] == [0.1, 0.1, 0.1]
    assert vectors[1][:3] == [0.2, 0.2, 0.2]


@pytest.mark.asyncio
async def test_embedding_client_truncates_oversized_vectors() -> None:
    oversized = [float(index) for index in range(settings.embedding_dimension + 4)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": oversized}],
                "model": settings.embedding_model,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        vectors = await EmbeddingClient(http_client=http_client).embed(["hello"])

    assert len(vectors[0]) == settings.embedding_dimension
    assert vectors[0] == oversized[: settings.embedding_dimension]


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
