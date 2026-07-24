import httpx
import pytest

from app.core.config import settings
from app.core.exceptions import APIError
from app.integrations.embedding_client import EmbeddingClient
from app.integrations.openai_compatible_client import OpenAICompatibleClient, build_api_url
from app.utils.llm_usage import get_llm_token_usage, reset_llm_token_usage


@pytest.mark.parametrize(
    ("base_url", "api_path", "expected"),
    [
        (
            "http://internal-llm:1234",
            "/v1/chat/completions",
            "http://internal-llm:1234/v1/chat/completions",
        ),
        (
            "http://internal-llm:1234/v1",
            "/v1/chat/completions",
            "http://internal-llm:1234/v1/chat/completions",
        ),
        (
            "https://internal-llm/gateway/v1/chat/completions",
            "/v1/chat/completions",
            "https://internal-llm/gateway/v1/chat/completions",
        ),
    ],
)
def test_build_api_url_supports_company_chat_completions_path(
    base_url: str,
    api_path: str,
    expected: str,
) -> None:
    assert build_api_url(base_url, api_path) == expected


def test_build_api_url_rejects_an_external_api_path_host() -> None:
    with pytest.raises(ValueError, match="LLM_API_PATH"):
        build_api_url("http://internal-llm:1234", "https://external.example/v1/chat/completions")


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
async def test_embedding_client_rejects_mismatched_vector_dimension() -> None:
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
        with pytest.raises(APIError) as exc_info:
            await EmbeddingClient(http_client=http_client).embed(["hello"])

    assert exc_info.value.error_code == "EMBEDDING_SERVICE_ERROR"
    assert exc_info.value.details == {
        "expected": settings.embedding_dimension,
        "actual": settings.embedding_dimension + 4,
    }


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
    assert b"reasoning_effort" not in captured["payload"]
    assert b"system" in captured["payload"]
    assert b"user" in captured["payload"]
    assert answer == "測試回答"


@pytest.mark.asyncio
async def test_llm_client_captures_provider_usage() -> None:
    reset_llm_token_usage()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-usage",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "usage ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "completion_tokens": 200,
                    "prompt_tokens": 43,
                    "total_tokens": 243,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        await OpenAICompatibleClient(http_client=http_client).chat_completion(
            system_prompt="system",
            user_prompt="user",
        )

    usage = get_llm_token_usage()
    assert usage.prompt_tokens == 43
    assert usage.completion_tokens == 200
    assert usage.total_tokens == 243


@pytest.mark.asyncio
async def test_llm_client_accumulates_usage_across_tool_call_rounds() -> None:
    reset_llm_token_usage()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(http_client=http_client)
        await client.chat_completion(system_prompt="system", user_prompt="first")
        await client.chat_completion(system_prompt="system", user_prompt="second")

    usage = get_llm_token_usage()
    assert usage.prompt_tokens == 20
    assert usage.completion_tokens == 10
    assert usage.total_tokens == 30


@pytest.mark.asyncio
async def test_llm_stream_requests_and_captures_usage() -> None:
    reset_llm_token_usage()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"streamed"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":12,'
                '"completion_tokens":3,"total_tokens":15}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(http_client=http_client)
        chunks = [
            chunk
            async for chunk in client.stream_chat_completion(
                system_prompt="system",
                user_prompt="user",
            )
        ]

    assert chunks == ["streamed"]
    assert b'"stream_options":{"include_usage":true}' in captured["payload"]
    usage = get_llm_token_usage()
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 3
    assert usage.total_tokens == 15


@pytest.mark.asyncio
async def test_llm_client_uses_configured_company_api_path(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "internal api ok"}}
                ]
            },
        )

    monkeypatch.setattr(settings, "llm_base_url", "http://company-llm.internal/v1")
    monkeypatch.setattr(settings, "llm_api_path", "/v1/chat/completions")
    monkeypatch.setattr(settings, "llm_api_key", "test-company-key")
    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http_client:
        answer = await OpenAICompatibleClient(http_client=http_client).chat_completion(
            system_prompt="system",
            user_prompt="connection test",
        )

    assert captured["url"] == "http://company-llm.internal/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-company-key"
    assert answer == "internal api ok"


@pytest.mark.asyncio
async def test_llm_client_only_sends_supported_reasoning_effort(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    monkeypatch.setattr(settings, "llm_reasoning_effort", "high")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        await OpenAICompatibleClient(http_client=http_client).chat_completion(
            system_prompt="system",
            user_prompt="user",
        )

    assert b'"reasoning_effort":"high"' in captured["payload"]
