from uuid import uuid4

from app.schemas.chat import ChatQueryResponse
from app.services.answer_parser_service import parse_structured_answer
from app.services.chat_response_service import finalize_chat_response
from app.utils.llm_usage import (
    LLMTokenUsage,
    record_llm_usage,
    reset_llm_token_usage,
)


def _response(answer: str) -> ChatQueryResponse:
    return ChatQueryResponse(
        request_id="request-1",
        session_id=uuid4(),
        message_id=uuid4(),
        answer=answer,
    )


def test_finalize_chat_response_moves_sections_out_of_answer_and_adds_usage() -> None:
    reset_llm_token_usage()
    record_llm_usage(
        LLMTokenUsage(
            prompt_tokens=43,
            completion_tokens=200,
            total_tokens=243,
        )
    )
    raw_answer = """### 1. Answer
The moon is about 384,400 kilometers from Earth.

### 2. Key points
- Its orbit is elliptical.
- The distance changes continuously.

### 3. Sources
- General astronomical knowledge

### 4. Confidence
High

### 5. Limitations
- This is an average distance.
"""

    response = finalize_chat_response(_response(raw_answer))

    assert response.answer == "The moon is about 384,400 kilometers from Earth."
    assert "### 2. Key points" not in response.answer
    assert "### 3. Sources" not in response.answer
    assert "### 4. Confidence" not in response.answer
    assert "### 5. Limitations" not in response.answer
    assert response.sections.key_points == [
        "Its orbit is elliptical.",
        "The distance changes continuously.",
    ]
    assert response.sections.sources == ["General astronomical knowledge"]
    assert response.sections.confidence == "High"
    assert response.sections.limitations == ["This is an average distance."]
    assert response.usage.prompt_tokens == 43
    assert response.usage.completion_tokens == 200
    assert response.usage.total_tokens == 243


def test_finalize_chat_response_keeps_plain_answers_and_uses_null_usage() -> None:
    reset_llm_token_usage()

    response = finalize_chat_response(_response("A plain provider response."))

    assert response.answer == "A plain provider response."
    assert response.sections.key_points == []
    assert response.sections.sources == []
    assert response.sections.confidence is None
    assert response.sections.limitations == []
    assert response.usage.prompt_tokens is None
    assert response.usage.completion_tokens is None
    assert response.usage.total_tokens is None


def test_parser_supports_localized_structured_headings() -> None:
    parsed = parse_structured_answer(
        """### 1. 回答
主要回答。
### 2. 重點
- 第一點
### 3. 來源
- 文件 A
### 4. 信心
- 中等
### 5. 限制
- 缺少即時資料
"""
    )

    assert parsed.answer == "主要回答。"
    assert parsed.key_points == ["第一點"]
    assert parsed.sources == ["文件 A"]
    assert parsed.confidence == "中等"
    assert parsed.limitations == ["缺少即時資料"]
