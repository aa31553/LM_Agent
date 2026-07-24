from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMTokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


_current_usage: ContextVar[LLMTokenUsage] = ContextVar(
    "lm_agent_llm_token_usage",
    default=LLMTokenUsage(),
)


def reset_llm_token_usage() -> None:
    _current_usage.set(LLMTokenUsage())


def add_llm_token_usage(
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
) -> LLMTokenUsage:
    current = _current_usage.get()
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
    return _current_usage.get()


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right
