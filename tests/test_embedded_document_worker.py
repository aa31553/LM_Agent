import asyncio

from fastapi.testclient import TestClient

import app.main as main_module


class FakeDocumentWorker:
    events: list[str] = []

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        self.events.append("started")
        await stop_event.wait()
        self.events.append("stopped")


def test_lifespan_runs_and_stops_embedded_document_worker(monkeypatch) -> None:
    FakeDocumentWorker.events = []
    monkeypatch.setattr(main_module, "ensure_database_ready", lambda: None)
    monkeypatch.setattr(main_module, "DocumentTasks", FakeDocumentWorker)
    monkeypatch.setattr(main_module.settings, "embedded_document_worker_enabled", True)

    with TestClient(main_module.create_app()) as client:
        assert client.get("/openapi.json").status_code == 200
        assert FakeDocumentWorker.events == ["started"]

    assert FakeDocumentWorker.events == ["started", "stopped"]


def test_lifespan_can_disable_embedded_document_worker(monkeypatch) -> None:
    class UnexpectedWorker:
        def __init__(self) -> None:
            raise AssertionError("disabled embedded worker must not be created")

    monkeypatch.setattr(main_module, "ensure_database_ready", lambda: None)
    monkeypatch.setattr(main_module, "DocumentTasks", UnexpectedWorker)
    monkeypatch.setattr(main_module.settings, "embedded_document_worker_enabled", False)

    with TestClient(main_module.create_app()) as client:
        assert client.get("/openapi.json").status_code == 200
