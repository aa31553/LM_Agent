import asyncio

import pytest

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.services.chat_runtime_service import ChatRuntimeService
from app.services.prompt_budget_service import PromptBudgetService


def test_code_context_is_truncated_with_head_and_tail(monkeypatch) -> None:
    monkeypatch.setattr(settings, "code_context_max_chars", 120)
    monkeypatch.setattr(settings, "code_context_max_tokens", 40)
    source = "BEGIN\n" + ("value = 1234567890\n" * 40) + "END"

    result, truncated = PromptBudgetService().truncate_code(source)

    assert truncated is True
    assert result.startswith("BEGIN")
    assert result.endswith("END")
    assert "code omitted" in result
    assert len(result) <= 120


def test_prompt_budget_drops_old_history_before_rejecting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_context_window_tokens", 500)
    monkeypatch.setattr(settings, "llm_max_tokens", 100)
    monkeypatch.setattr(settings, "llm_prompt_safety_margin_tokens", 50)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old question " * 80},
        {"role": "assistant", "content": "old answer " * 80},
        {"role": "user", "content": "current question"},
    ]

    result = PromptBudgetService().fit_messages(messages)

    assert result.dropped_history_messages == 2
    assert [message["role"] for message in result.messages] == ["system", "user"]
    assert result.messages[-1]["content"] == "current question"


def test_prompt_budget_rejects_oversized_current_request(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_context_window_tokens", 500)
    monkeypatch.setattr(settings, "llm_max_tokens", 100)
    monkeypatch.setattr(settings, "llm_prompt_safety_margin_tokens", 50)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "x" * 5000},
    ]

    with pytest.raises(APIError) as exc_info:
        PromptBudgetService().fit_messages(messages)

    assert exc_info.value.error_code == ErrorCode.PROMPT_TOO_LARGE
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_chat_runtime_applies_end_to_end_timeout(monkeypatch) -> None:
    monkeypatch.setattr(settings, "chat_max_concurrent_requests", 1)
    monkeypatch.setattr(settings, "chat_request_timeout_seconds", 0.01)
    service = ChatRuntimeService()

    with pytest.raises(APIError) as exc_info:
        async with service.request_slot():
            await asyncio.sleep(0.05)

    assert exc_info.value.error_code == ErrorCode.CHAT_TIMEOUT
    assert exc_info.value.status_code == 504


@pytest.mark.asyncio
async def test_chat_runtime_returns_busy_after_queue_deadline(monkeypatch) -> None:
    monkeypatch.setattr(settings, "chat_max_concurrent_requests", 1)
    monkeypatch.setattr(settings, "chat_queue_timeout_seconds", 0.01)
    service = ChatRuntimeService()

    async with service.request_slot():
        with pytest.raises(APIError) as exc_info:
            async with service.request_slot():
                pytest.fail("second request should not acquire the only slot")

    assert exc_info.value.error_code == ErrorCode.CHAT_BUSY
    assert exc_info.value.status_code == 503
