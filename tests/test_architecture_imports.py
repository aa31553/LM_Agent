def test_app_factory_imports() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/v1/health" in paths
    assert "/api/v1/chat/query" in paths
    assert "/api/v1/documents/upload" in paths


def test_dlp_masks_secrets() -> None:
    from app.services.masking_service import MaskingService

    result = MaskingService().scan_and_mask("token=secret-value and user@example.com", "query")
    assert "[REDACTED]" in result.text
    assert "[EMAIL]" in result.text
