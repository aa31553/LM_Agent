from typing import TYPE_CHECKING

from app.agent_runtime.legacy import LegacyAgentRuntime
from app.core.config import settings

if TYPE_CHECKING:
    from app.services.agent_tool_service import AgentToolService


def create_agent_runtime(gateway: "AgentToolService"):
    """Create the configured runtime without leaking implementation details to routes."""

    if settings.agent_runtime == "pydantic_ai":
        from app.agent_runtime.pydantic_ai_runtime import PydanticAIAgentRuntime

        return PydanticAIAgentRuntime(gateway=gateway, retries=settings.agent_runtime_retries)
    return LegacyAgentRuntime(gateway)
