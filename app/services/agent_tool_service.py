import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.integrations.openai_compatible_client import ChatToolCall
from app.repositories.document_repository import DocumentRepository
from app.schemas.chat import ToolCallTrace
from app.services.keyword_search_service import KeywordSearchService
from app.services.llm_service import LLMService
from app.services.permission_service import PermissionService
from app.services.prompt_budget_service import PromptBudgetService
from app.services.rerank_service import RerankService


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
        first = await self.llm_service.complete_messages(
            messages=messages,
            tools=self.tool_schemas(),
            tool_choice="auto",
        )
        if not first.tool_calls:
            return AgentAnswer(
                answer=first.content,
                tool_calls=[],
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        selected_tool_calls = first.tool_calls[: self.max_tool_calls]
        messages.append(
            {
                "role": "assistant",
                "content": first.content or None,
                "tool_calls": [self._openai_tool_call_payload(item) for item in selected_tool_calls],
            }
        )
        traces: list[ToolCallTrace] = []
        remaining_tool_chars = settings.agent_tool_context_max_chars
        for tool_call in selected_tool_calls:
            arguments = self._decode_arguments(tool_call.arguments)
            result = await self.execute_tool(
                tool_name=tool_call.name,
                arguments=arguments,
                knowledge_base_ids=knowledge_base_ids,
                top_k=top_k,
                use_rerank=use_rerank,
                principal=principal,
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

        if self.db is not None:
            self.db.commit()
        self.prompt_budget_service.validate_messages(messages)
        final = await self.llm_service.complete_messages(messages=messages)
        return AgentAnswer(
            answer=final.content or first.content,
            tool_calls=traces,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict,
        knowledge_base_ids: list[UUID],
        top_k: int,
        use_rerank: bool,
        principal: Principal,
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
        return {"error": f"Unknown tool: {tool_name}"}

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

    def tool_schemas(self) -> list[dict]:
        return [
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
