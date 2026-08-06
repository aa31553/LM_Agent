from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from app.core.security import Principal
from app.services.agent_tool_service import AgentAnswer


class AgentRuntime(Protocol):
    """Stable application contract implemented by legacy and PydanticAI runtimes."""

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
    ) -> AgentAnswer: ...
