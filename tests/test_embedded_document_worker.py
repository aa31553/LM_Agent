from fastapi.testclient import TestClient

import app.main as main_module


def test_api_lifespan_does_not_start_document_worker(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "ensure_database_ready", lambda: None)

    with TestClient(main_module.create_app()) as client:
        assert client.get("/openapi.json").status_code == 200
