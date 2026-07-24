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
    assert "/api/v1/admin/llm/test" in paths
    assert "/api/v1/chat/sessions/{session_id}" in paths


def test_api_documentation_uses_only_local_static_assets() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
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


def test_dlp_masks_secrets() -> None:
    from app.services.masking_service import MaskingService

    result = MaskingService().scan_and_mask("token=secret-value and user@example.com", "query")
    assert "[REDACTED]" in result.text
    assert "[EMAIL]" in result.text
