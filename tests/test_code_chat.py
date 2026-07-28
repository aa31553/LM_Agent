from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.constants import ChatType, ConfidentialLevel
from app.core.exceptions import APIError
from app.core.security import Principal
from app.db.base import Base
from app.db.session import create_database_engine, get_db
from app.integrations.openai_compatible_client import ChatCompletionResult
from app.main import create_app
from app.models.chat import ChatSession
from app.schemas.chat import CodeChatRequest
from app.services.code_chat_service import CodeChatService
from app.services.llmwiki_service import LLMWikiService
from app.services.rag_service import RAGService


class CodeLLM:
    answer = """### 1. Answer
The null value is dereferenced before it is checked.
### 2. Diagnosis
The database connection may be None. Confidence: high
### 3. Suggested changes
#### Validate the connection first
Check the connection before calling execute.
```python
if connection is None:
    raise RuntimeError("database is unavailable")
result = connection.execute(query)
```
### 4. Risks
- This changes the failure mode to an explicit error.
### 5. Code blocks
```python
def run(connection, query):
    if connection is None:
        raise RuntimeError("database is unavailable")
    return connection.execute(query)
```
"""

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete_messages(self, messages, tools=None, tool_choice=None):
        self.requests.append(messages)
        assert "internal company code assistant" in messages[0]["content"]
        assert "Supplied code" in messages[-1]["content"]
        return ChatCompletionResult(content=self.answer, tool_calls=[])

    async def stream_complete_messages(self, messages):
        self.requests.append(messages)
        assert "internal company code assistant" in messages[0]["content"]
        yield self.answer[:80]
        yield self.answer[80:]


@pytest.fixture
def code_client(tmp_path: Path, monkeypatch):
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "local_storage_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(LLMWikiService, "_ensure_schema", lambda _self: None)

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), session_factory
    engine.dispose()


def _principal() -> Principal:
    return Principal(
        external_user_id="code-user",
        username="code-user",
        department="engineering",
        roles={"employee"},
        clearance_level=ConfidentialLevel.INTERNAL,
    )


def _payload() -> CodeChatRequest:
    return CodeChatRequest(
        query="Why does this database helper fail?",
        code="result = connection.execute(query)",
        language="python",
        file_name="database.py",
    )


@pytest.mark.asyncio
async def test_code_chat_returns_structured_response_and_code_session(code_client) -> None:
    _, session_factory = code_client
    with session_factory() as db:
        service = CodeChatService(db)
        service.rag_service.llm_service = CodeLLM()
        response = await service.answer(_payload(), "code-test", _principal())

        assert response.chat_type == ChatType.CODE
        assert response.answer == "The null value is dereferenced before it is checked."
        assert response.diagnosis.summary == "The database connection may be None. Confidence: high"
        assert response.diagnosis.confidence == "high"
        assert response.risks == ["This changes the failure mode to an explicit error."]
        assert len(response.suggested_changes) == 1
        assert len(response.code_blocks) == 2
        assert db.get(ChatSession, response.session_id).chat_type == ChatType.CODE.value

        with pytest.raises(APIError, match="cannot be used"):
            await RAGService(db).answer(
                _payload().model_copy(update={"session_id": response.session_id}),
                "general-test",
                _principal(),
            )


@pytest.mark.asyncio
async def test_code_chat_stream_returns_structured_done_response(code_client) -> None:
    _, session_factory = code_client
    with session_factory() as db:
        service = CodeChatService(db)
        service.rag_service.llm_service = CodeLLM()
        events = [event async for event in service.stream_answer(_payload(), "stream-test", _principal())]

        assert [event["event"] for event in events] == ["start", "delta", "delta", "done"]
        assert events[-1]["response"].chat_type == ChatType.CODE
        assert events[-1]["response"].code_blocks[0].language == "python"


@pytest.mark.asyncio
async def test_code_chat_includes_prior_session_messages(code_client) -> None:
    _, session_factory = code_client
    with session_factory() as db:
        service = CodeChatService(db)
        llm = CodeLLM()
        service.rag_service.llm_service = llm
        first = await service.answer(_payload(), "code-history-1", _principal())
        follow_up = _payload().model_copy(
            update={
                "session_id": first.session_id,
                "query": "How should I test that fix?",
                "code": None,
            }
        )
        await service.answer(follow_up, "code-history-2", _principal())

        roles = [message["role"] for message in llm.requests[-1]]
        assert roles == ["system", "user", "assistant", "user"]
        assert "Why does this database helper fail?" in llm.requests[-1][1]["content"]
        assert CodeLLM.answer.strip() == llm.requests[-1][2]["content"]
        assert "How should I test that fix?" in llm.requests[-1][-1]["content"]


def test_code_file_upload_creates_code_session(code_client) -> None:
    client, session_factory = code_client
    response = client.post(
        "/api/v1/documents/upload",
        headers={"Authorization": "Bearer admin", "X-Request-ID": "code-upload"},
        files={"file": ("example.py", b"print('hello')\n", "text/x-python")},
        data={"scope": "session", "chat_type": "code", "confidential_level": "internal"},
    )

    assert response.status_code == 200
    body = response.json()
    with session_factory() as db:
        session = db.get(ChatSession, UUID(body["session_id"]))
        assert session is not None
        assert session.chat_type == ChatType.CODE.value


def test_code_chat_routes_are_published_in_openapi(code_client) -> None:
    client, _ = code_client
    paths = client.app.openapi()["paths"]
    assert "/api/v1/code-chat/query" in paths
    assert "/api/v1/code-chat/stream" in paths


def test_code_chat_sse_uses_real_event_delimiters(code_client, monkeypatch) -> None:
    client, _ = code_client

    async def fake_stream_answer(self, payload, request_id, principal):
        yield {"event": "start", "request_id": request_id}
        yield {"event": "delta", "text": "ok"}

    monkeypatch.setattr(CodeChatService, "stream_answer", fake_stream_answer)
    response = client.post(
        "/api/v1/code-chat/stream",
        headers={"Authorization": "Bearer admin", "X-Request-ID": "sse-format"},
        json=_payload().model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: start\ndata: " in response.text
    assert "\n\nevent: delta\ndata: " in response.text
    assert "\\ndata:" not in response.text


def test_code_assistant_defaults_to_no_permanent_knowledge_base() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")

    assert 'const selectedCodeKbId = codeNode.value;' in source
    assert '"不使用技術知識庫"' in source
    assert 'const kbId = $("#codeKbId").value;' in source
    assert 'const kbId = $("#codeKbId").value || state.selectedKbId;' not in source
