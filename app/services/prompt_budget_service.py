import json
import math
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError


@dataclass(frozen=True)
class PromptBudgetResult:
    messages: list[dict[str, Any]]
    estimated_tokens: int
    dropped_history_messages: int


class PromptBudgetService:
    """Apply a conservative budget before sending messages to an upstream LLM."""

    code_truncation_marker = "\n\n[... code omitted to fit the configured prompt budget ...]\n\n"

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        ascii_chars = sum(character.isascii() for character in text)
        non_ascii_chars = len(text) - ascii_chars
        lexical_tokens = len(text.split())
        character_estimate = math.ceil(ascii_chars / 4) + non_ascii_chars
        return max(1, lexical_tokens, character_estimate)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        total = 3
        for message in messages:
            total += 4
            content = message.get("content")
            if isinstance(content, str):
                total += self.estimate_tokens(content)
            elif content is not None:
                total += self.estimate_tokens(
                    json.dumps(content, ensure_ascii=False, separators=(",", ":"))
                )
            if message.get("tool_calls"):
                total += self.estimate_tokens(
                    json.dumps(
                        message["tool_calls"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
        return total

    def truncate_code(self, code: str) -> tuple[str, bool]:
        max_chars = settings.code_context_max_chars
        max_tokens = settings.code_context_max_tokens
        if len(code) <= max_chars and self.estimate_tokens(code) <= max_tokens:
            return code, False

        low = 0
        high = min(len(code), max(0, max_chars - len(self.code_truncation_marker)))
        while low < high:
            keep = (low + high + 1) // 2
            candidate = self._code_excerpt(code, keep)
            if self.estimate_tokens(candidate) <= max_tokens:
                low = keep
            else:
                high = keep - 1

        return self._code_excerpt(code, low), True

    def _code_excerpt(self, code: str, keep: int) -> str:
        head_size = math.ceil(keep * 0.7)
        tail_size = keep - head_size
        tail = code[-tail_size:] if tail_size else ""
        return f"{code[:head_size]}{self.code_truncation_marker}{tail}"

    def max_prompt_tokens(self, *, reserved_tokens: int = 0) -> int:
        return (
            settings.llm_context_window_tokens
            - settings.llm_max_tokens
            - settings.llm_prompt_safety_margin_tokens
            - reserved_tokens
        )

    def fit_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        reserved_tokens: int = 0,
    ) -> PromptBudgetResult:
        max_prompt_tokens = self.max_prompt_tokens(reserved_tokens=reserved_tokens)
        if max_prompt_tokens < 256:
            raise APIError(
                ErrorCode.PROMPT_TOO_LARGE,
                "The configured LLM prompt budget is too small.",
                status_code=500,
                details={
                    "context_window_tokens": settings.llm_context_window_tokens,
                    "max_output_tokens": settings.llm_max_tokens,
                    "safety_margin_tokens": settings.llm_prompt_safety_margin_tokens,
                    "additional_reserved_tokens": reserved_tokens,
                },
            )

        fitted = [dict(message) for message in messages]
        dropped = 0
        estimated = self.estimate_messages(fitted)
        while estimated > max_prompt_tokens and len(fitted) > 2:
            fitted.pop(1)
            dropped += 1
            if len(fitted) > 2 and fitted[1].get("role") == "assistant":
                fitted.pop(1)
                dropped += 1
            estimated = self.estimate_messages(fitted)

        if estimated > max_prompt_tokens:
            raise APIError(
                ErrorCode.PROMPT_TOO_LARGE,
                "The request is too large for the configured LLM context window.",
                status_code=413,
                details={
                    "estimated_prompt_tokens": estimated,
                    "max_prompt_tokens": max_prompt_tokens,
                    "context_window_tokens": settings.llm_context_window_tokens,
                    "max_output_tokens": settings.llm_max_tokens,
                    "safety_margin_tokens": settings.llm_prompt_safety_margin_tokens,
                },
            )

        return PromptBudgetResult(
            messages=fitted,
            estimated_tokens=estimated,
            dropped_history_messages=dropped,
        )

    def validate_messages(self, messages: list[dict[str, Any]]) -> int:
        estimated = self.estimate_messages(messages)
        max_prompt_tokens = self.max_prompt_tokens()
        if estimated > max_prompt_tokens:
            raise APIError(
                ErrorCode.PROMPT_TOO_LARGE,
                "Tool results exceed the configured LLM context window.",
                status_code=413,
                details={
                    "estimated_prompt_tokens": estimated,
                    "max_prompt_tokens": max_prompt_tokens,
                },
            )
        return estimated
