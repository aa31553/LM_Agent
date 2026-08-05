import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.integrations.openai_compatible_client import ChatCompletionResult, ChatToolCall
from app.schemas.chat import ChatQueryRequest
from app.services.agent_tool_service import AgentToolService
from app.services.workspace_file_ingestion_service import WorkspaceFileIngestionService
from app.storage.workspace_storage import WorkspaceStorage


def test_structured_workspace_file_is_saved_as_markdown_table(tmp_path: Path) -> None:
    workspace_id = uuid4()
    file_id = uuid4()
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps(
            [
                {"name": "alpha", "count": 10},
                {"name": "beta", "count": 20},
            ]
        ),
        encoding="utf-8",
    )
    storage = WorkspaceStorage(root=str(tmp_path / "storage"))

    result = WorkspaceFileIngestionService(storage).process(
        workspace_id=workspace_id,
        file_id=file_id,
        file_path=str(source),
        file_type="json",
    )

    manifest = result["dataset_manifest"]
    content = Path(manifest["representation_path"]).read_text(encoding="utf-8")
    assert manifest["representation_format"] == "markdown_table"
    assert "| name | count |" in content
    assert "| alpha | 10 |" in content


def test_spreadsheet_keeps_datasets_and_adds_cursor_readable_markdown(tmp_path: Path) -> None:
    workspace_id = uuid4()
    file_id = uuid4()
    source = tmp_path / "measurements.csv"
    source.write_text("bucket,value\n0,3\n10,7\n", encoding="utf-8")
    storage = WorkspaceStorage(root=str(tmp_path / "storage"))

    result = WorkspaceFileIngestionService(storage).process(
        workspace_id=workspace_id,
        file_id=file_id,
        file_path=str(source),
        file_type="csv",
    )

    manifest = result["dataset_manifest"]
    assert manifest["datasets"]
    assert Path(manifest["datasets"][0]["path"]).suffix == ".parquet"
    first = storage.read_text_segment(
        manifest["representation_path"],
        offset=0,
        max_chars=24,
    )
    assert first is not None
    assert first["content"].startswith("# measurements.csv")
    if first["next_offset"] is not None:
        second = storage.read_text_segment(
            manifest["representation_path"],
            offset=first["next_offset"],
            max_chars=200,
        )
        assert second is not None
        assert "bucket" in second["content"]


@pytest.mark.asyncio
async def test_agent_can_list_then_read_workspace_file_across_tool_rounds(monkeypatch) -> None:
    file_id = uuid4()

    class FakeLLM:
        def __init__(self) -> None:
            self.responses = iter(
                [
                    ChatCompletionResult(
                        content="",
                        tool_calls=[
                            ChatToolCall(
                                id="list-1",
                                name="list_workspace_files",
                                arguments="{}",
                            )
                        ],
                    ),
                    ChatCompletionResult(
                        content="",
                        tool_calls=[
                            ChatToolCall(
                                id="read-1",
                                name="read_workspace_file",
                                arguments=json.dumps({"file_id": str(file_id)}),
                            )
                        ],
                    ),
                    ChatCompletionResult(content="done", tool_calls=[]),
                ]
            )

        async def complete_messages(self, **_kwargs):
            return next(self.responses)

    service = AgentToolService(llm_service=FakeLLM())
    calls = []

    async def fake_execute_tool(**kwargs):
        calls.append(kwargs["tool_name"])
        return {"ok": True}

    monkeypatch.setattr(service, "execute_tool", fake_execute_tool)
    workspace_id = uuid4()
    answer = await service.answer_with_tools(
        system_prompt="Use tools when necessary.",
        user_prompt="Read the workspace file.",
        knowledge_base_ids=[],
        workspace_id=workspace_id,
        top_k=8,
        use_rerank=False,
        principal=None,
    )

    assert answer.answer == "done"
    assert calls == ["list_workspace_files", "read_workspace_file"]
    assert [trace.tool_name for trace in answer.tool_calls] == calls


def test_chat_contract_accepts_workspace_and_publishes_workspace_tools() -> None:
    workspace_id = uuid4()
    payload = ChatQueryRequest(query="read it", workspace_id=workspace_id, use_tools=True)
    schemas = AgentToolService(llm_service=object()).tool_schemas(workspace_id=workspace_id)

    assert payload.workspace_id == workspace_id
    names = {schema["function"]["name"] for schema in schemas}
    assert {"list_workspace_files", "read_workspace_file"} <= names
