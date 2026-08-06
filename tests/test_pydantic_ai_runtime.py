import asyncio
import subprocess
import sys
from importlib import metadata
from types import SimpleNamespace

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

from app.agent_runtime.outputs import AnswerOutput, to_legacy_answer
from app.agent_runtime.pydantic_ai_runtime import (
    AgentDependencies,
    PydanticAIAgentRuntime,
    _usage_payload,
)
from app.scripts.validate_pydantic_ai import EXPECTED_VERSION, validate_installation


class FakeGateway:
    max_tool_calls = 4
    db = None
    llm_service = SimpleNamespace(
        route=SimpleNamespace(
            api_path="/v1/chat/completions",
            base_url="http://localhost",
            api_key="",
            ssl_verify=False,
            timeout_seconds=5,
            model="test",
        )
    )
    prompt_budget_service = SimpleNamespace(validate_messages=lambda _messages: None)

    async def execute_tool(self, **_kwargs):
        return {"ok": True}

    @staticmethod
    def _bounded_tool_content(result, _remaining_chars):
        return str(result)


def _runtime() -> PydanticAIAgentRuntime:
    return PydanticAIAgentRuntime(gateway=FakeGateway())


def test_installed_pydantic_ai_matches_runtime_contract() -> None:
    assert metadata.version("pydantic-ai-slim") == EXPECTED_VERSION
    assert validate_installation() == []


def test_legacy_app_startup_does_not_import_pydantic_ai() -> None:
    script = """
import builtins
original_import = builtins.__import__

def blocked(name, *args, **kwargs):
    if name.startswith('pydantic_ai'):
        raise AssertionError(f'unexpected eager import: {name}')
    return original_import(name, *args, **kwargs)

builtins.__import__ = blocked
import app.main
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


async def test_runtime_builds_canonical_openai_chat_model() -> None:
    runtime = _runtime()
    async with httpx.AsyncClient(trust_env=False) as http_client:
        model = runtime._build_model(FakeGateway.llm_service.route, http_client)

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "test"


def test_workspace_tools_are_only_registered_for_workspace_requests() -> None:
    runtime = _runtime()
    without_workspace = runtime._build_agent(
        model=TestModel(call_tools=[]),
        system_prompt="test",
        workspace_enabled=False,
    )
    with_workspace = runtime._build_agent(
        model=TestModel(call_tools=[]),
        system_prompt="test",
        workspace_enabled=True,
    )

    assert set(without_workspace._function_toolset.tools) == {
        "search_documents",
        "get_document_status",
    }
    assert set(with_workspace._function_toolset.tools) == {
        "search_documents",
        "get_document_status",
        "list_workspace_files",
        "read_workspace_file",
    }


def test_typed_agent_can_run_without_calling_external_model() -> None:
    runtime = _runtime()
    agent = runtime._build_agent(
        model=TestModel(call_tools=[], custom_output_text="done"),
        system_prompt="test",
        workspace_enabled=False,
    )
    deps = AgentDependencies(
        gateway=FakeGateway(),
        knowledge_base_ids=[],
        top_k=8,
        use_rerank=False,
        principal=None,
        workspace_id=None,
        traces=[],
    )

    result = asyncio.run(agent.run("hello", deps=deps))

    assert result.output == "done"
    assert _usage_payload(result.usage)["requests"] == 1


def test_runtime_answer_contract_can_be_verified_with_pydantic_test_model() -> None:
    class TestRuntime(PydanticAIAgentRuntime):
        @staticmethod
        def _build_model(_route, _http_client):
            return TestModel(call_tools=[], custom_output_text="typed runtime answer")

    async def run():
        return await TestRuntime(gateway=FakeGateway()).answer_with_tools(
            system_prompt="test",
            user_prompt="hello",
            knowledge_base_ids=[],
            top_k=8,
            use_rerank=False,
            principal=None,
        )

    result = asyncio.run(run())

    assert result.answer == "typed runtime answer"
    assert result.tool_calls == []


def test_runtime_stream_emits_contract_events_with_pydantic_test_model() -> None:
    class TestRuntime(PydanticAIAgentRuntime):
        @staticmethod
        def _build_model(_route, _http_client):
            return TestModel(call_tools=[], custom_output_text="streamed answer")

    async def run():
        return [
            event
            async for event in TestRuntime(gateway=FakeGateway()).stream_answer_with_tools(
                system_prompt="test",
                user_prompt="hello",
                knowledge_base_ids=[],
                top_k=8,
                use_rerank=False,
                principal=None,
            )
        ]

    events = asyncio.run(run())

    assert events[-1]["event"] == "complete"
    assert events[-1]["answer"] == "streamed answer"
    assert "".join(event["text"] for event in events if event["event"] == "delta")


def test_structured_answer_keeps_public_markdown_compatibility() -> None:
    output = AnswerOutput(
        answer="The result is 42.",
        key_points=["42 is the bounded result."],
        sources=["workspace.csv"],
        confidence="high",
        limitations=["No causal inference."],
    )

    rendered = to_legacy_answer(output)

    assert "### 1. Answer" in rendered
    assert "### 3. Sources" in rendered
    assert "workspace.csv" in rendered
