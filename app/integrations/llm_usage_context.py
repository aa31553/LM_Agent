from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMTokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


_usage_context: ContextVar[LLMTokenUsage | None] = ContextVar(
    "llm_usage_context",
    default=None,
)


def reset_llm_usage() -> None:
    _usage_context.set(None)


def record_llm_usage(usage: LLMTokenUsage | None) -> None:
    if usage is None:
        return
    current = _usage_context.get()
    if current is None:
        _usage_context.set(usage)
        return
    _usage_context.set(
        LLMTokenUsage(
            prompt_tokens=current.prompt_tokens + usage.prompt_tokens,
            completion_tokens=current.completion_tokens + usage.completion_tokens,
            total_tokens=current.total_tokens + usage.total_tokens,
        )
    )


def get_llm_usage() -> LLMTokenUsage | None:
    return _usage_context.get()
