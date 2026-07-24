from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMTokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


_current_usage: ContextVar[LLMTokenUsage | None] = ContextVar(
    "lm_agent_llm_token_usage",
    default=None,
)


def reset_llm_token_usage() -> None:
    _current_usage.set(None)


def add_llm_token_usage(
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
) -> LLMTokenUsage:
    current = _current_usage.get() or LLMTokenUsage()
    merged_prompt = _sum_optional(current.prompt_tokens, prompt_tokens)
    merged_completion = _sum_optional(current.completion_tokens, completion_tokens)
    merged_total = _sum_optional(current.total_tokens, total_tokens)
    if merged_total is None and merged_prompt is not None and merged_completion is not None:
        merged_total = merged_prompt + merged_completion
    merged = LLMTokenUsage(
        prompt_tokens=merged_prompt,
        completion_tokens=merged_completion,
        total_tokens=merged_total,
    )
    _current_usage.set(merged)
    return merged


def get_llm_token_usage() -> LLMTokenUsage:
    return _current_usage.get() or LLMTokenUsage()


def record_llm_usage(usage: LLMTokenUsage | None) -> None:
    if usage is None:
        return
    add_llm_token_usage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )


def parse_llm_usage(raw_usage: Any) -> LLMTokenUsage | None:
    """Parse the standard OpenAI-compatible usage object without inventing values."""

    if not isinstance(raw_usage, dict):
        return None
    prompt_tokens = _non_negative_int(
        raw_usage.get("prompt_tokens", raw_usage.get("input_tokens"))
    )
    completion_tokens = _non_negative_int(
        raw_usage.get("completion_tokens", raw_usage.get("output_tokens"))
    )
    total_tokens = _non_negative_int(raw_usage.get("total_tokens"))
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    return LLMTokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None
