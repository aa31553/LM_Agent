from collections.abc import Sequence
from typing import Any
from uuid import UUID

from app.core.security import Principal
from app.services.agent_tool_service import AgentAnswer, AgentToolService


class LegacyAgentRuntime:
    """Adapter preserving the pre-PydanticAI tool loop for rollback and comparison."""

    def __init__(self, gateway: AgentToolService) -> None:
        self.gateway = gateway

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
        return await self.gateway._answer_with_tools_legacy(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            knowledge_base_ids=knowledge_base_ids,
            top_k=top_k,
            use_rerank=use_rerank,
            principal=principal,
            workspace_id=workspace_id,
            messages=list(messages) if messages is not None else None,
        )
