import pytest
from fastapi.testclient import TestClient

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.main import create_app
from app.services.llm_service import LLMService


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin", "X-Request-ID": "llm-test-request"}


@pytest.mark.asyncio
async def test_admin_llm_test_bypasses_database_and_knowledge_bases(monkeypatch) -> None:
    captured: dict = {}

    async def fake_complete(self, system_prompt, user_prompt, image_paths=None):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["image_paths"] = image_paths
        return "company llm is reachable"

    monkeypatch.setattr(LLMService, "complete", fake_complete)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/admin/llm/test",
        headers=_admin_headers(),
        json={
            "system_prompt": "You are a software engineer.",
            "message": "Run a connection test.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["answer"] == "company llm is reachable"
    assert body["endpoint"].endswith("/v1/chat/completions")
    assert body["latency_ms"] >= 0
    assert "api_key" not in body
    assert "token" not in body
    assert captured == {
        "system_prompt": "You are a software engineer.",
        "user_prompt": "Run a connection test.",
        "image_paths": [],
    }


def test_admin_llm_test_requires_admin_role(monkeypatch) -> None:
    async def should_not_run(*args, **kwargs):
        raise AssertionError("LLM must not be called for a non-admin request")

    monkeypatch.setattr(LLMService, "complete", should_not_run)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/admin/llm/test",
        headers={"Authorization": "Bearer reader|qa|internal|reader"},
        json={"message": "test"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


def test_admin_llm_test_requires_bearer_token(monkeypatch) -> None:
    async def should_not_run(*args, **kwargs):
        raise AssertionError("LLM must not be called without a bearer token")

    monkeypatch.setattr(LLMService, "complete", should_not_run)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/admin/llm/test",
        json={"message": "test"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "UNAUTHORIZED"


def test_admin_llm_test_returns_upstream_api_error(monkeypatch) -> None:
    async def failed_complete(*args, **kwargs):
        raise APIError(
            ErrorCode.LLM_SERVICE_ERROR,
            "LLM service returned an error.",
            status_code=502,
            details={"status_code": 401},
        )

    monkeypatch.setattr(LLMService, "complete", failed_complete)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/admin/llm/test",
        headers=_admin_headers(),
        json={"message": "test"},
    )

    assert response.status_code == 502
    assert response.json()["error_code"] == "LLM_SERVICE_ERROR"
    assert response.json()["details"] == {"status_code": 401}
