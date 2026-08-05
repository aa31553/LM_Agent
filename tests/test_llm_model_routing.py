import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.chat import _rag_service
from app.api.v1.code_chat import _code_chat_service
from app.core.config import LLMModelRouteSettings, Settings, settings
from app.core.constants import ConfidentialLevel, ErrorCode, ThinkingMode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.integrations.openai_compatible_client import OpenAICompatibleClient
from app.main import create_app
from app.schemas.chat import ChatQueryRequest, CodeChatRequest
from app.services.llm_routing_service import LLMRoutingService


@pytest.fixture
def configured_routes(monkeypatch):
    routes = {
        "general": LLMModelRouteSettings(
            base_url="http://general-llm.internal:8000",
            model="company/general-31b",
            api_key="general-secret",
            ssl_verify=False,
            allowed_thinking_modes=["default", "none"],
        ),
        "reasoning": LLMModelRouteSettings(
            base_url="https://reasoning-llm.internal/gateway/v1",
            api_path="/v1/chat/completions",
            model="company/reasoning-32b",
            api_key="reasoning-secret",
            temperature=0.2,
            top_p=0.8,
            max_tokens=4096,
            reasoning_effort="medium",
        ),
    }
    monkeypatch.setattr(settings, "llm_model_routes", routes)
    monkeypatch.setattr(settings, "llm_default_route", "general")
    return routes


def test_chat_request_models_publish_model_and_thinking_mode() -> None:
    chat = ChatQueryRequest(query="hello", model="reasoning", thinking_mode="high")
    code = CodeChatRequest(query="review", model="general", thinking_mode="none")

    assert chat.model == "reasoning"
    assert chat.thinking_mode == ThinkingMode.HIGH
    assert code.model == "general"
    assert code.thinking_mode == ThinkingMode.NONE

    with pytest.raises(ValidationError):
        ChatQueryRequest(query="hello", thinking_mode="unlimited")


def test_model_routes_are_loaded_from_json_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "LLM_MODEL_ROUTES",
        json.dumps(
            {
                "reasoning": {
                    "base_url": "https://reasoning.internal",
                    "model": "company/reasoning-32b",
                    "allowed_thinking_modes": ["default", "high"],
                }
            }
        ),
    )

    configured = Settings(_env_file=None).llm_model_routes["reasoning"]

    assert configured.base_url == "https://reasoning.internal"
    assert configured.model == "company/reasoning-32b"
    assert configured.allowed_thinking_modes == ["default", "high"]


def test_all_chat_query_and_stream_endpoints_use_routable_request_models() -> None:
    openapi = TestClient(create_app()).app.openapi()
    schemas = openapi["components"]["schemas"]
    assert {"model", "thinking_mode"} <= set(schemas["ChatQueryRequest"]["properties"])
    assert {"model", "thinking_mode"} <= set(schemas["CodeChatRequest"]["properties"])

    for path in (
        "/api/v1/chat/query",
        "/api/v1/chat/stream",
        "/api/v1/code-chat/query",
        "/api/v1/code-chat/stream",
    ):
        assert "requestBody" in openapi["paths"][path]["post"]


def test_chat_and_code_endpoint_factories_forward_the_selected_route(configured_routes) -> None:
    chat_payload = ChatQueryRequest(
        query="hello",
        model="reasoning",
        thinking_mode="high",
    )
    code_payload = CodeChatRequest(
        query="review",
        model="general",
        thinking_mode="none",
    )

    chat_service = _rag_service(None, chat_payload, _principal())
    code_service = _code_chat_service(None, code_payload, _principal())

    assert chat_service.llm_service.client.route.selection == "reasoning"
    assert chat_service.llm_service.client.route.reasoning_effort == "high"
    assert code_service.rag_service.llm_service.client.route.selection == "general"
    assert code_service.rag_service.llm_service.client.route.reasoning_effort == ""


def test_router_uses_default_route_and_rejects_unknown_model(configured_routes) -> None:
    default_route = LLMRoutingService().resolve()
    assert default_route.selection == "general"
    assert default_route.model == "company/general-31b"
    assert default_route.reasoning_effort == ""

    with pytest.raises(APIError) as exc_info:
        LLMRoutingService().resolve("https://attacker.example/v1/chat/completions")

    assert exc_info.value.error_code == ErrorCode.INVALID_REQUEST
    assert exc_info.value.details["available_models"] == ["general", "reasoning"]


def test_router_enforces_model_thinking_mode_allowlist(configured_routes) -> None:
    with pytest.raises(APIError) as exc_info:
        LLMRoutingService().resolve("general", ThinkingMode.HIGH)

    assert exc_info.value.error_code == ErrorCode.INVALID_REQUEST
    assert exc_info.value.details["available_thinking_modes"] == ["default", "none"]


@pytest.mark.asyncio
async def test_non_stream_request_routes_model_endpoint_key_and_reasoning(configured_routes) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        answer = await OpenAICompatibleClient(
            http_client=http_client,
            model="reasoning",
            thinking_mode=ThinkingMode.HIGH,
        ).chat_completion(system_prompt="system", user_prompt="user")

    assert answer == "ok"
    assert captured["url"] == "https://reasoning-llm.internal/gateway/v1/chat/completions"
    assert captured["authorization"] == "Bearer reasoning-secret"
    assert captured["payload"] == {
        "model": "company/reasoning-32b",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "temperature": 0.2,
        "top_p": 0.8,
        "max_tokens": 4096,
        "reasoning_effort": "high",
    }


@pytest.mark.asyncio
async def test_stream_request_routes_endpoint_and_omits_disabled_reasoning(configured_routes) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        chunks = [
            chunk
            async for chunk in OpenAICompatibleClient(
                http_client=http_client,
                model="general",
                thinking_mode=ThinkingMode.NONE,
            ).stream_chat_completion_messages([{"role": "user", "content": "hello"}])
        ]

    assert chunks == ["ok"]
    assert captured["url"] == "http://general-llm.internal:8000/v1/chat/completions"
    assert captured["payload"]["model"] == "company/general-31b"
    assert captured["payload"]["stream"] is True
    assert "reasoning_effort" not in captured["payload"]


def _principal() -> Principal:
    return Principal(
        external_user_id="routing-user",
        username="routing-user",
        department="engineering",
        roles={"employee"},
        clearance_level=ConfidentialLevel.INTERNAL,
    )
