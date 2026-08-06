import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ConfidentialLevel, ErrorCode, PermissionLevel
from app.core.exceptions import APIError
from app.core.security import Principal
from app.integrations.openai_compatible_client import ChatToolCall
from app.models.analysis import AnalysisFile
from app.repositories.document_repository import DocumentRepository
from app.schemas.chat import ToolCallTrace
from app.services.keyword_search_service import KeywordSearchService
from app.services.llm_service import LLMService
from app.services.masking_service import MaskingService
from app.services.permission_service import PermissionService
from app.services.prompt_budget_service import PromptBudgetService
from app.services.rerank_service import RerankService
from app.services.workspace_service import WorkspaceService
from app.storage.workspace_storage import WorkspaceStorage


@dataclass(frozen=True)
class AgentAnswer:
    answer: str
    tool_calls: list[ToolCallTrace]
    latency_ms: int


class AgentToolService:
    max_tool_calls = 4

    def __init__(
        self,
        db: Session | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        self.db = db
        self.llm_service = llm_service or LLMService()
        self.permission_service = PermissionService(db)
        self.prompt_budget_service = PromptBudgetService()

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
        messages: list[dict[str, Any]] | None = None,
    ) -> AgentAnswer:
        from app.agent_runtime.factory import create_agent_runtime

        return await create_agent_runtime(self).answer_with_tools(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            knowledge_base_ids=knowledge_base_ids,
            top_k=top_k,
            use_rerank=use_rerank,
            principal=principal,
            workspace_id=workspace_id,
            messages=messages,
        )

    async def _answer_with_tools_legacy(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
        workspace_id: UUID | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> AgentAnswer:
        started = time.perf_counter()
        messages = list(messages or [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        messages = self.prompt_budget_service.fit_messages(
            messages,
            reserved_tokens=settings.agent_tool_prompt_reserve_tokens,
        ).messages
        schemas = self.tool_schemas(workspace_id=workspace_id)
        response = await self.llm_service.complete_messages(
            messages=messages,
            tools=schemas,
            tool_choice="auto",
        )
        if not response.tool_calls:
            return AgentAnswer(
                answer=response.content,
                tool_calls=[],
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        traces: list[ToolCallTrace] = []
        remaining_tool_chars = settings.agent_tool_context_max_chars
        fallback_answer = response.content or ""
        while response.tool_calls and len(traces) < self.max_tool_calls:
            selected_tool_calls = response.tool_calls[
                : self.max_tool_calls - len(traces)
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or None,
                    "tool_calls": [
                        self._openai_tool_call_payload(item) for item in selected_tool_calls
                    ],
                }
            )
            for tool_call in selected_tool_calls:
                arguments = self._decode_arguments(tool_call.arguments)
                result = await self.execute_tool(
                    tool_name=tool_call.name,
                    arguments=arguments,
                    knowledge_base_ids=knowledge_base_ids,
                    top_k=top_k,
                    use_rerank=use_rerank,
                    principal=principal,
                    workspace_id=workspace_id,
                )
                traces.append(
                    ToolCallTrace(
                        tool_name=tool_call.name,
                        arguments=arguments,
                        result=result,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": self._bounded_tool_content(result, remaining_tool_chars),
                    }
                )
                remaining_tool_chars = max(
                    0,
                    remaining_tool_chars - len(messages[-1]["content"]),
                )

            self.prompt_budget_service.validate_messages(messages)
            response = await self.llm_service.complete_messages(
                messages=messages,
                tools=schemas,
                tool_choice="auto",
            )
            fallback_answer = response.content or fallback_answer

        if self.db is not None:
            self.db.commit()
        self.prompt_budget_service.validate_messages(messages)
        if response.tool_calls:
            final = await self.llm_service.complete_messages(messages=messages)
            answer = final.content or fallback_answer
        else:
            answer = response.content or fallback_answer
        return AgentAnswer(
            answer=answer,
            tool_calls=traces,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_answer_with_tools(self, **kwargs) -> AsyncIterator[dict[str, Any]]:
        """Yield runtime-neutral streaming events for tool-enabled responses."""

        from app.agent_runtime.factory import create_agent_runtime

        runtime = create_agent_runtime(self)
        stream_method = getattr(runtime, "stream_answer_with_tools", None)
        if stream_method is None:
            result = await runtime.answer_with_tools(**kwargs)
            if result.answer:
                yield {"event": "delta", "text": result.answer}
            yield {
                "event": "complete",
                "answer": result.answer,
                "tool_calls": result.tool_calls,
                "latency_ms": result.latency_ms,
            }
            return
        async for event in stream_method(**kwargs):
            yield event

    async def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
        workspace_id: UUID | None = None,
    ) -> dict:
        if tool_name == "search_documents":
            return await self._search_documents(
                arguments=arguments,
                knowledge_base_ids=knowledge_base_ids,
                top_k=top_k,
                use_rerank=use_rerank,
                principal=principal,
            )
        if tool_name == "get_document_status":
            return self._get_document_status(arguments=arguments, principal=principal)
        if tool_name == "list_workspace_files":
            return self._list_workspace_files(
                workspace_id=workspace_id,
                principal=principal,
                arguments=arguments,
            )
        if tool_name == "read_workspace_file":
            return self._read_workspace_file(
                workspace_id=workspace_id,
                principal=principal,
                arguments=arguments,
            )
        return {"error": f"Unknown tool: {tool_name}"}

    def _list_workspace_files(
        self,
        *,
        workspace_id: UUID | None,
        principal: Principal,
        arguments: dict,
    ) -> dict:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        if workspace_id is None:
            return {"error": "workspace_id is required"}
        WorkspaceService(self.db).get(workspace_id, principal, PermissionLevel.READ)
        try:
            limit = max(1, min(int(arguments.get("limit", 50)), 100))
        except (TypeError, ValueError):
            limit = 50
        sources = list(
            self.db.scalars(
                select(AnalysisFile)
                .where(AnalysisFile.workspace_id == workspace_id)
                .order_by(AnalysisFile.created_at.desc())
                .limit(limit)
            )
        )
        items = []
        for source in sources:
            if source.expires_at is not None and source.expires_at <= datetime.utcnow():
                continue
            try:
                self.permission_service.ensure_level_access(
                    principal,
                    ConfidentialLevel(source.confidential_level),
                )
            except APIError:
                continue
            manifest = source.dataset_manifest or {}
            items.append(
                {
                    "file_id": str(source.id),
                    "filename": source.original_filename,
                    "file_type": source.file_type,
                    "status": source.status,
                    "representation_format": manifest.get("representation_format"),
                    "llm_readable": bool(manifest.get("representation_path")),
                    "analysis_ready": bool(manifest.get("datasets")),
                    "size_bytes": source.size_bytes,
                }
            )
        return {"workspace_id": str(workspace_id), "files": items}

    def _read_workspace_file(
        self,
        *,
        workspace_id: UUID | None,
        principal: Principal,
        arguments: dict,
    ) -> dict:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        if workspace_id is None:
            return {"error": "workspace_id is required"}
        try:
            file_id = UUID(str(arguments.get("file_id") or ""))
        except ValueError:
            return {"error": "file_id must be a UUID"}
        source = self.db.get(AnalysisFile, file_id)
        if source is None or (
            source.expires_at is not None and source.expires_at <= datetime.utcnow()
        ):
            return {"error": "workspace file not found"}
        WorkspaceService(self.db).get(source.workspace_id, principal, PermissionLevel.READ)
        self.permission_service.ensure_level_access(
            principal,
            ConfidentialLevel(source.confidential_level),
        )
        if source.workspace_id != workspace_id:
            return {"error": "file does not belong to the active workspace"}
        manifest = source.dataset_manifest or {}
        representation_path = manifest.get("representation_path")
        if source.status != "ready" or not representation_path:
            return {
                "error": "workspace file is not ready for reading",
                "status": source.status,
            }
        try:
            cursor = max(0, int(arguments.get("cursor", 0)))
            requested_chars = int(arguments.get("max_chars", 6000))
        except (TypeError, ValueError):
            return {"error": "cursor and max_chars must be integers"}
        max_chars = max(
            1,
            min(requested_chars, settings.agent_tool_context_max_chars, 8000),
        )
        segment = WorkspaceStorage().read_text_segment(
            representation_path,
            offset=cursor,
            max_chars=max_chars,
        )
        if segment is None:
            return {"error": "workspace representation is unavailable"}
        dlp = MaskingService(self.db).scan_and_mask(segment["content"], location="context")
        if dlp.blocked:
            return {"error": "workspace file segment was blocked by DLP"}
        return {
            "workspace_id": str(workspace_id),
            "file_id": str(source.id),
            "filename": source.original_filename,
            "representation_format": manifest.get("representation_format"),
            **segment,
            "content": dlp.text,
        }

    async def _search_documents(
        self,
        *,
        arguments: dict,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
    ) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        requested_kbs = self._allowed_knowledge_base_ids(arguments, knowledge_base_ids)
        requested_top_k = self._bounded_top_k(arguments.get("top_k"), default=top_k)
        chunks = await self._retrieve(
            query=query,
            knowledge_base_ids=requested_kbs,
            top_k=requested_top_k,
            use_rerank=use_rerank,
            principal=principal,
        )
        return {
            "query": query,
            "knowledge_base_ids": [str(item) for item in requested_kbs],
            "results": [
                {
                    "chunk_id": str(chunk.chunk_id),
                    "document_id": str(chunk.document_id),
                    "score": chunk.final_score,
                    "content": chunk.content[:1200],
                    "metadata": chunk.metadata or {},
                }
                for chunk in chunks
            ],
        }

    def _get_document_status(self, *, arguments: dict, principal: Principal) -> dict:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        raw_document_id = str(arguments.get("document_id") or "")
        try:
            document_id = UUID(raw_document_id)
        except ValueError:
            return {"error": "document_id must be a UUID"}
        document = DocumentRepository(self.db).get(document_id)
        if document is None:
            return {"error": "document not found"}
        self.permission_service.ensure_document_read(principal, document)
        return {
            "document_id": str(document.id),
            "title": document.title,
            "filename": document.original_filename or document.filename,
            "file_type": document.file_type,
            "status": document.status,
            "page_count": document.page_count,
            "chunk_count": document.chunk_count,
            "error_message": document.error_message,
        }

    async def _retrieve(
        self,
        *,
        query: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
    ):
        if self.db is None or not knowledge_base_ids:
            return []
        keyword_results = await KeywordSearchService(db=self.db).search(
            query=query,
            knowledge_base_ids=knowledge_base_ids,
            top_k=top_k,
            principal=principal,
        )
        ranked = sorted(keyword_results, key=lambda chunk: chunk.final_score, reverse=True)
        if use_rerank:
            return await RerankService().rerank(query, ranked, top_k)
        return ranked[:top_k]

    def _allowed_knowledge_base_ids(self, arguments: dict, allowed_ids: list[UUID]) -> list[UUID]:
        requested = arguments.get("knowledge_base_ids")
        if not isinstance(requested, list):
            return allowed_ids
        allowed = set(allowed_ids)
        parsed: list[UUID] = []
        for item in requested:
            try:
                value = UUID(str(item))
            except ValueError:
                continue
            if value in allowed:
                parsed.append(value)
        return parsed or allowed_ids

    def _bounded_top_k(self, value, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(1, min(parsed, 20))

    def _decode_arguments(self, raw_arguments: str) -> dict:
        try:
            decoded = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _openai_tool_call_payload(self, tool_call: ChatToolCall) -> dict:
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            },
        }

    def _bounded_tool_content(self, result: dict, remaining_chars: int) -> str:
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized) <= remaining_chars:
            return serialized
        if remaining_chars <= 0:
            return ""
        marker = '\n{"truncated":true}'
        if remaining_chars <= len(marker):
            return marker[:remaining_chars]
        keep = max(0, remaining_chars - len(marker))
        return f"{serialized[:keep]}{marker}"

    def tool_schemas(self, *, workspace_id: UUID | None = None) -> list[dict]:
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "search_documents",
                    "description": "Search indexed internal document chunks within the current request knowledge bases.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "knowledge_base_ids": {
                                "type": "array",
                                "items": {"type": "string", "format": "uuid"},
                            },
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_document_status",
                    "description": "Get ingestion status and indexing counts for one document the user can read.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "document_id": {"type": "string", "format": "uuid"},
                        },
                        "required": ["document_id"],
                    },
                },
            },
        ]
        if workspace_id is not None:
            schemas.extend(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "list_workspace_files",
                            "description": (
                                "List files in the active workspace before choosing a file to read."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                                },
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "read_workspace_file",
                            "description": (
                                "Read one LLM-ready workspace file segment. Continue with the returned "
                                "next_offset cursor only when more content is needed."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "file_id": {"type": "string", "format": "uuid"},
                                    "cursor": {"type": "integer", "minimum": 0},
                                    "max_chars": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 8000,
                                    },
                                },
                                "required": ["file_id"],
                            },
                        },
                    },
                ]
            )
        return schemas
