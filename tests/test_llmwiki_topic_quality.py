from uuid import uuid4

import pytest

from app.core.constants import ConfidentialLevel
from app.core.security import Principal
from app.services.llmwiki_service import LLMWikiService, _WikiChunk


def _principal() -> Principal:
    return Principal(
        external_user_id="topic-quality-test",
        username="topic-quality-test",
        department="qa",
        roles={"admin"},
        clearance_level=ConfidentialLevel.INTERNAL,
    )


def _chunk(content: str, section_title: str = "Thermal runaway propagation") -> _WikiChunk:
    return _WikiChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        knowledge_base_id=uuid4(),
        content=content,
        document_title="Lithium Ion Battery Transportation Safety",
        section_title=section_title,
        page_start=1,
        page_end=1,
        confidential_level=ConfidentialLevel.INTERNAL.value,
        source_type="pdf_text",
        metadata={"title": section_title},
        created_at=None,
        score=1.0,
    )


def test_topic_discovery_filters_stopwords_urls_and_reference_noise() -> None:
    service = LLMWikiService()
    chunks = [
        _chunk(
            "The and org doi com https journal et al references "
            "thermal runaway propagation in lithium ion batteries under mechanical abuse."
        )
    ]

    signals = service._topic_signals_from_chunks(chunks, limit=20)
    topics = {signal.topic.lower() for signal in signals}

    assert "the" not in topics
    assert "and" not in topics
    assert "org" not in topics
    assert "doi" not in topics
    assert "journal" not in topics
    assert any("thermal runaway propagation" in topic for topic in topics)
    assert any("mechanical abuse" in topic for topic in topics)


class InvalidJsonLLM:
    async def complete(self, system_prompt: str, user_prompt: str, image_paths=None) -> str:
        return "not json"


@pytest.mark.asyncio
async def test_llm_review_invalid_json_rejects_candidate() -> None:
    service = LLMWikiService(llm_service=InvalidJsonLLM())
    signal = service._topic_signals_from_chunks(
        [_chunk("Transportation standards and testing protocols govern battery shipping.")],
        limit=5,
    )[0]

    review = await service._llm_review_signal(signal)

    assert review.decision == "rejected"
    assert "llm_review_failed" in review.rejection_reason
