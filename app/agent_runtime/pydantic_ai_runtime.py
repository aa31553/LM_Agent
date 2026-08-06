"""PydanticAI implementation of LM_Agent's constrained tool runtime.

The runtime deliberately receives an ``AgentToolService`` gateway.  It never
opens files, queries the ORM, or constructs a provider from environment
variables itself; those responsibilities remain behind the existing service
and route policy boundaries.
"""

import dataclasses
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from app.agent_runtime.outputs import AnswerOutput, to_legacy_answer
from app.core.config import settings
from app.core.security import Principal
from app.schemas.chat import ToolCallTrace
from app.services.agent_tool_service import AgentAnswer, AgentToolService
from app.services.llm_service import LLMService
from app.utils.llm_usage import parse_llm_usage, record_llm_usage


class AgentDependencies:
    """Request-local dependencies exposed to typed PydanticAI tools."""

    def __init__(
        self,
        *,
        gateway: AgentToolService,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
        workspace_id: UUID | None,
        traces: list[ToolCallTrace],
    ) -> None:
        self.gateway = gateway
        self.knowledge_base_ids = knowledge_base_ids
        self.top_k = top_k
        self.use_rerank = use_rerank
        self.principal = principal
        self.workspace_id = workspace_id
        self.traces = traces


class PydanticAIAgentRuntime:
    """Typed tool loop backed by the existing OpenAI-compatible route policy."""

    def __init__(self, *, gateway: AgentToolService, retries: int = 1) -> None:
        self.gateway = gateway
        self.llm_service: LLMService = gateway.llm_service
        self.retries = max(0, min(retries, 3))

    async def answer_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
        workspace_id: UUID | None = None,
        messages: Sequence[dict[str, Any]] | None = None,
    ) -> AgentAnswer:
        started = time.perf_counter()
        traces: list[ToolCallTrace] = []
        deps = AgentDependencies(
            gateway=self.gateway,
            knowledge_base_ids=knowledge_base_ids,
            top_k=top_k,
            use_rerank=use_rerank,
            principal=principal,
            workspace_id=workspace_id,
            traces=traces,
        )
        route = self.llm_service.route
        history = self._message_history(messages or [], user_prompt)

        async with httpx.AsyncClient(
            verify=route.ssl_verify,
            timeout=route.timeout_seconds,
            trust_env=False,
        ) as http_client:
            model = self._build_model(route, http_client)
            agent = self._build_agent(
                model=model,
                system_prompt=system_prompt,
                workspace_enabled=workspace_id is not None,
                model_settings=self._model_settings(route),
            )
            result = await agent.run(
                user_prompt=user_prompt,
                message_history=history,
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=self.gateway.max_tool_calls + 2,
                    tool_calls_limit=self.gateway.max_tool_calls,
                ),
                retries=self.retries,
            )
            record_llm_usage(parse_llm_usage(_usage_payload(result.usage)))
            if isinstance(result.output, AnswerOutput):
                answer = to_legacy_answer(result.output)
            else:
                answer = result.output if isinstance(result.output, str) else str(result.output)

        self.gateway.prompt_budget_service.validate_messages(
            list(messages or []) + [{"role": "assistant", "content": answer}]
        )
        if self.gateway.db is not None:
            self.gateway.db.commit()
        return AgentAnswer(
            answer=answer,
            tool_calls=traces,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_answer_with_tools(self, **kwargs) -> AsyncIterator[dict[str, Any]]:
        """Stream text deltas while keeping third-party events behind our contract."""

        started = time.perf_counter()
        traces: list[ToolCallTrace] = []
        deps = AgentDependencies(
            gateway=self.gateway,
            knowledge_base_ids=kwargs["knowledge_base_ids"],
            top_k=kwargs["top_k"],
            use_rerank=kwargs["use_rerank"],
            principal=kwargs["principal"],
            workspace_id=kwargs.get("workspace_id"),
            traces=traces,
        )
        route = self.llm_service.route
        history = self._message_history(kwargs.get("messages") or [], kwargs["user_prompt"])
        async with httpx.AsyncClient(
            verify=route.ssl_verify,
            timeout=route.timeout_seconds,
            trust_env=False,
        ) as http_client:
            agent = self._build_agent(
                model=self._build_model(route, http_client),
                system_prompt=kwargs["system_prompt"],
                workspace_enabled=deps.workspace_id is not None,
                model_settings=self._model_settings(route),
            )
            async with agent.run_stream(
                user_prompt=kwargs["user_prompt"],
                message_history=history,
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=self.gateway.max_tool_calls + 2,
                    tool_calls_limit=self.gateway.max_tool_calls,
                ),
                retries=self.retries,
            ) as result:
                if settings.agent_structured_output:
                    output = await result.get_output()
                    answer = (
                        to_legacy_answer(output)
                        if isinstance(output, AnswerOutput)
                        else str(output)
                    )
                    if answer:
                        yield {"event": "delta", "text": answer}
                else:
                    parts: list[str] = []
                    async for delta in result.stream_text(delta=True):
                        parts.append(delta)
                        yield {"event": "delta", "text": delta}
                    answer = "".join(parts)
                    if not answer:
                        answer = str(await result.get_output())
                record_llm_usage(parse_llm_usage(_usage_payload(result.usage)))
        if self.gateway.db is not None:
            self.gateway.db.commit()
        yield {
            "event": "complete",
            "answer": answer,
            "tool_calls": traces,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    def _build_model(self, route, http_client: httpx.AsyncClient) -> OpenAIChatModel:
        completion_root = route.api_path.removesuffix("/chat/completions").rstrip("/")
        provider = OpenAIProvider(
            base_url=self._join_url(route.base_url, completion_root),
            api_key=route.api_key or "lm-agent",
            http_client=http_client,
        )
        return OpenAIChatModel(route.model, provider=provider)

    @staticmethod
    def _model_settings(route) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "temperature": getattr(route, "temperature", None),
                "top_p": getattr(route, "top_p", None),
                "max_tokens": getattr(route, "max_tokens", None),
            }.items()
            if value is not None
        }

    def _build_agent(
        self,
        *,
        model: OpenAIChatModel,
        system_prompt: str,
        workspace_enabled: bool,
        model_settings: dict[str, Any] | None = None,
    ) -> Agent:
        agent = Agent(
            model=model,
            deps_type=AgentDependencies,
            output_type=AnswerOutput if settings.agent_structured_output else str,
            instructions=system_prompt,
            model_settings=model_settings,
            retries=self.retries,
            name="lm-agent-secure-tools",
            tool_timeout=settings.chat_request_timeout_seconds,
        )

        @agent.tool(sequential=True)
        async def search_documents(
            ctx: RunContext[AgentDependencies],
            query: str,
            knowledge_base_ids: list[UUID] | None = None,
            top_k: int | None = None,
        ) -> str:
            return await self._execute(
                ctx,
                "search_documents",
                {
                    "query": query,
                    "knowledge_base_ids": knowledge_base_ids,
                    "top_k": top_k,
                },
            )

        @agent.tool(sequential=True)
        async def get_document_status(
            ctx: RunContext[AgentDependencies],
            document_id: UUID,
        ) -> str:
            return await self._execute(
                ctx,
                "get_document_status",
                {"document_id": str(document_id)},
            )

        # Workspace tools are registered only for a request with an active,
        # already-authorized workspace id.
        if workspace_enabled:

            @agent.tool(sequential=True)
            async def list_workspace_files(
                ctx: RunContext[AgentDependencies],
                limit: int = 50,
            ) -> str:
                return await self._execute(ctx, "list_workspace_files", {"limit": limit})

            @agent.tool(sequential=True)
            async def read_workspace_file(
                ctx: RunContext[AgentDependencies],
                file_id: UUID,
                cursor: int = 0,
                max_chars: int = 6000,
            ) -> str:
                return await self._execute(
                    ctx,
                    "read_workspace_file",
                    {
                        "file_id": str(file_id),
                        "cursor": cursor,
                        "max_chars": max_chars,
                    },
                )

        return agent

    async def _execute(
        self,
        ctx: RunContext[AgentDependencies],
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        result = await ctx.deps.gateway.execute_tool(
            tool_name=name,
            arguments=arguments,
            knowledge_base_ids=ctx.deps.knowledge_base_ids,
            top_k=ctx.deps.top_k,
            use_rerank=ctx.deps.use_rerank,
            principal=ctx.deps.principal,
            workspace_id=ctx.deps.workspace_id,
        )
        ctx.deps.traces.append(
            ToolCallTrace(tool_name=name, arguments=arguments, result=result)
        )
        return ctx.deps.gateway._bounded_tool_content(
            result,
            settings.agent_tool_context_max_chars,
        )

    @staticmethod
    def _join_url(base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.strip('/')}" if path else base_url.rstrip("/")

    @staticmethod
    def _message_history(
        messages: Sequence[dict[str, Any]],
        user_prompt: str,
    ) -> list[Any]:
        """Convert the stable subset of our message contract to PydanticAI messages."""

        source = list(messages)
        if source and source[-1].get("role") == "user":
            source = source[:-1]
        converted: list[Any] = []
        for message in source:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                continue
            if role == "user":
                converted.append(ModelRequest(parts=[UserPromptPart(content=_text(content))]))
            elif role == "assistant" and isinstance(content, str) and content:
                converted.append(ModelResponse(parts=[TextPart(content=content)]))
            elif role == "tool" and isinstance(content, str):
                converted.append(
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name="legacy_tool",
                                tool_call_id=str(message.get("tool_call_id") or "legacy"),
                                content=content,
                            )
                        ]
                    )
                )
        return ModelMessagesTypeAdapter.validate_python(converted)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return str(content or "")


def _usage_payload(usage: Any) -> dict[str, Any]:
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if dataclasses.is_dataclass(usage):
        return dataclasses.asdict(usage)
    return {}
