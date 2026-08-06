"""Application-owned boundary around the selectable agent runtimes.

Only this package is allowed to know about the PydanticAI implementation.  The
rest of LM_Agent continues to exchange the small ``AgentAnswer`` contract used
by the existing chat, code-chat, and analysis services.
"""

from app.agent_runtime.factory import create_agent_runtime
from app.agent_runtime.protocol import AgentRuntime

__all__ = ["AgentRuntime", "create_agent_runtime"]
