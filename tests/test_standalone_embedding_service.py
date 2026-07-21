from pathlib import Path

from fastapi.testclient import TestClient

from services.embedding_service.app import create_app
from services.embedding_service.config import EmbeddingServiceSettings
from services.embedding_service.runtime import EmbeddingRuntime


class FakeBackend:
    model_name = "test-embedding"
    device = "cpu"
    dimension = 3

    def load(self) -> None:
        return None

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index), 0.5] for index, _text in enumerate(texts)]

    def count_tokens(self, texts: list[str]) -> int:
        return sum(len(text.split()) for text in texts)


def settings(api_key: str = "") -> EmbeddingServiceSettings:
    return EmbeddingServiceSettings(
        model_path=Path("unused"),
        model_name="test-embedding",
        device="cpu",
        normalize_embeddings=True,
        max_batch_size=4,
        max_input_chars=1000,
        api_key=api_key,
        host="127.0.0.1",
        port=1234,
    )


def test_openai_compatible_embedding_and_monitoring_endpoints() -> None:
    config = settings()
    runtime = EmbeddingRuntime(config, backend=FakeBackend())
    with TestClient(create_app(config, runtime)) as client:
        response = client.post(
            "/v1/embeddings",
            json={"model": "test-embedding", "input": ["hello world", "你好"]},
        )
        status_response = client.get("/status")
        metrics_response = client.get("/metrics")

    assert response.status_code == 200
    assert response.json()["object"] == "list"
    assert len(response.json()["data"]) == 2
    assert len(response.json()["data"][0]["embedding"]) == 3
    assert response.json()["usage"]["prompt_tokens"] == 3
    assert status_response.json()["requests_total"] == 1
    assert status_response.json()["dimension"] == 3
    assert "embedding_requests_total 1" in metrics_response.text


def test_api_key_batch_limit_and_dimension_validation() -> None:
    config = settings(api_key="secret")
    runtime = EmbeddingRuntime(config, backend=FakeBackend())
    with TestClient(create_app(config, runtime)) as client:
        assert client.get("/status").status_code == 401
        headers = {"Authorization": "Bearer secret"}
        too_many = client.post(
            "/v1/embeddings",
            headers=headers,
            json={"input": ["a", "b", "c", "d", "e"]},
        )
        wrong_dimension = client.post(
            "/v1/embeddings",
            headers=headers,
            json={"input": "hello", "dimensions": 2},
        )

    assert too_many.status_code == 400
    assert wrong_dimension.status_code == 400


def test_documentation_uses_only_local_static_assets() -> None:
    config = settings()
    runtime = EmbeddingRuntime(config, backend=FakeBackend())
    with TestClient(create_app(config, runtime)) as client:
        swagger = client.get("/docs")
        redoc = client.get("/redoc")

        assert swagger.status_code == 200
        assert redoc.status_code == 200
        assert "/docs-assets/swagger-ui-bundle.js" in swagger.text
        assert "/docs-assets/swagger-ui.css" in swagger.text
        assert "/docs-assets/redoc.standalone.js" in redoc.text
        assert "https://" not in swagger.text
        assert "http://" not in swagger.text
        assert "https://" not in redoc.text
        assert "http://" not in redoc.text

        for asset_path in (
            "/docs-assets/swagger-ui-bundle.js",
            "/docs-assets/swagger-ui.css",
            "/docs-assets/redoc.standalone.js",
            "/docs-assets/favicon-32x32.png",
        ):
            response = client.get(asset_path)
            assert response.status_code == 200
            assert response.content
            if not asset_path.endswith(".png"):
                assert response.headers["content-encoding"] == "gzip"

        assert client.get("/docs/oauth2-redirect").status_code == 200
        assert client.get("/openapi.json").status_code == 200
