from uuid import uuid4

from app.models.chat import ChatMessage
from app.services.chat_history_service import ChatHistoryService
from app.services.masking_service import MaskingService


def _message(role: str, content: str, *, original: str | None = None) -> ChatMessage:
    return ChatMessage(
        id=uuid4(),
        session_id=uuid4(),
        role=role,
        original_content=original,
        masked_content=content,
        final_content=content,
    )


def test_history_uses_last_six_turns_and_re_masks_content() -> None:
    messages: list[ChatMessage] = []
    for index in range(7):
        messages.extend(
            [
                _message(
                    "user",
                    f"question {index} from user{index}@example.com",
                    original=f"original secret question {index}",
                ),
                _message("assistant", f"answer {index}"),
            ]
        )
    current = _message("user", "current question")
    messages.append(current)

    history = ChatHistoryService(
        MaskingService(),
        max_turns=6,
        max_chars=8000,
    ).build(messages, exclude_message_id=current.id)

    assert len(history.messages) == 12
    assert history.messages[0]["content"].startswith("question 1")
    assert "[EMAIL]" in history.messages[0]["content"]
    assert all("original secret" not in item["content"] for item in history.messages)
    assert all("current question" not in item["content"] for item in history.messages)
    assert history.truncated is True


def test_history_keeps_newest_turn_within_character_budget() -> None:
    previous_user = _message("user", "u" * 30)
    previous_assistant = _message("assistant", "a" * 30)
    current = _message("user", "current")

    history = ChatHistoryService(
        MaskingService(),
        max_turns=6,
        max_chars=40,
    ).build(
        [previous_user, previous_assistant, current],
        exclude_message_id=current.id,
    )

    assert history.char_count <= 40
    assert history.messages[0]["role"] == "user"
    assert history.truncated is True
