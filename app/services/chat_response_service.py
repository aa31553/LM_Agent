from app.schemas.chat import AnswerSections, ChatQueryResponse, LLMUsage
from app.services.answer_parser_service import parse_structured_answer
from app.utils.llm_usage import get_llm_token_usage


def finalize_chat_response(response: ChatQueryResponse) -> ChatQueryResponse:
    parsed = parse_structured_answer(response.answer)
    usage = get_llm_token_usage()
    response.answer = parsed.answer
    response.sections = AnswerSections(
        key_points=parsed.key_points,
        sources=parsed.sources,
        confidence=parsed.confidence,
        limitations=parsed.limitations,
    )
    response.usage = LLMUsage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
    return response
