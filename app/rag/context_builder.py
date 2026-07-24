from app.core.config import settings
from app.services.vector_store_service import RetrievedChunk
from app.utils.token_counter import count_tokens


class ContextBuilder:
    def build(
        self,
        chunks: list[RetrievedChunk],
        max_tokens: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        context, _used_chunks = self.build_with_used_chunks(
            chunks,
            max_tokens=max_tokens,
            max_chars=max_chars,
        )
        return context

    def build_with_used_chunks(
        self,
        chunks: list[RetrievedChunk],
        max_tokens: int | None = None,
        max_chars: int | None = None,
    ) -> tuple[str, list[RetrievedChunk]]:
        token_budget = max_tokens or settings.context_max_tokens
        char_budget = max_chars or settings.context_max_chars
        sections: list[str] = []
        used_chunks: list[RetrievedChunk] = []
        used_tokens = 0
        used_chars = 0
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.metadata or {}
            title = metadata.get("title", str(chunk.document_id))
            page_start = metadata.get("page_start", "")
            page_end = metadata.get("page_end", page_start)
            header = "\n".join(
                [
                    f"[Source {index}]",
                    f"Document: {title}",
                    f"Page: {page_start}-{page_end}",
                    f"Chunk ID: {chunk.chunk_id}",
                    "Content:",
                ]
            )
            remaining_chars = char_budget - used_chars - len(header) - 2
            if remaining_chars <= 0 or used_tokens >= token_budget:
                break

            content = self._truncate_content(
                chunk.content,
                remaining_tokens=token_budget - used_tokens,
                remaining_chars=remaining_chars,
            )
            if not content:
                break

            section = f"{header}\n{content}"
            sections.append(section)
            used_chunks.append(chunk)
            used_tokens += count_tokens(content)
            used_chars += len(section) + 2
        return "\n\n".join(sections), used_chunks

    def _truncate_content(
        self,
        content: str,
        remaining_tokens: int,
        remaining_chars: int,
    ) -> str:
        if remaining_tokens <= 0 or remaining_chars <= 0:
            return ""

        char_limited = content[:remaining_chars]
        words = char_limited.split()
        if not words:
            return char_limited
        if len(words) <= remaining_tokens:
            return char_limited
        return " ".join(words[:remaining_tokens])
