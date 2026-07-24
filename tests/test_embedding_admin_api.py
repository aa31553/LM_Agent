import pytest
from fastapi.testclient import TestClient

from app.integrations.embedding_client import EmbeddingClient
from app.main import create_app
from app.services.embedding_service import EmbeddingService


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin", "X-Request-ID": "embedding-test-request"}


def test_admin_embedding_status_is_database_independent(monkeypatch) -> None:
    async def fake_status(self):
        return {
            "status": "ok",
            "model_state": "ready",
            "model": "test-model",
            "device": "cuda:0",
            "dimension": 1024,
            "requests_total": 4,
        }

    monkeypatch.setattr(EmbeddingClient, "status", fake_status)
    client = TestClient(create_app())
    response = client.get("/api/v1/admin/embedding/status", headers=_admin_headers())

    assert response.status_code == 200
    assert response.json()["service"]["device"] == "cuda:0"
    assert response.json()["service"]["requests_total"] == 4
    assert "api_key" not in response.text.lower()


def test_admin_embedding_test_reports_dimension_norm_and_preview(monkeypatch) -> None:
    async def fake_embed(self, texts):
        assert texts == ["AOI test"]
        return [[0.6, 0.8] + [0.0] * 1022]

    monkeypatch.setattr(EmbeddingService, "embed_texts", fake_embed)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/admin/embedding/test",
        headers=_admin_headers(),
        json={"text": "AOI test"},
    )

    assert response.status_code == 200
    assert response.json()["actual_dimension"] == 1024
    assert response.json()["vector_norm"] == 1.0
    assert response.json()["vector_preview"][:2] == [0.6, 0.8]


@pytest.mark.parametrize("path", ["status", "test"])
def test_embedding_admin_endpoints_require_admin(path, monkeypatch) -> None:
    async def should_not_run(*args, **kwargs):
        raise AssertionError("Embedding service must not be called for a non-admin request")

    monkeypatch.setattr(EmbeddingClient, "status", should_not_run)
    monkeypatch.setattr(EmbeddingService, "embed_texts", should_not_run)
    client = TestClient(create_app())
    response = (
        client.get(
            f"/api/v1/admin/embedding/{path}",
            headers={"Authorization": "Bearer reader|qa|internal|reader"},
        )
        if path == "status"
        else client.post(
            f"/api/v1/admin/embedding/{path}",
            headers={"Authorization": "Bearer reader|qa|internal|reader"},
            json={"text": "test"},
        )
    )

    assert response.status_code == 403
