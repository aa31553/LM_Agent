import re
from collections.abc import AsyncIterator

from app.core.constants import ChatType, ThinkingMode
from app.core.security import Principal
from app.schemas.chat import (
    CodeBlock,
    CodeChatRequest,
    CodeChatResponse,
    CodeDiagnosis,
    SuggestedCodeChange,
)
from app.services.prompt_budget_service import PromptBudgetService
from app.services.rag_service import RAGService
from app.utils.llm_usage import get_llm_token_usage

_SECTION_PATTERN = re.compile(
    r"^\s*#{1,6}\s*(?:[1-5]\s*[.)-]?\s*)?"
    r"(?P<name>answer|diagnosis|suggested\s+changes|risks|code\s+blocks)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_BLOCK_PATTERN = re.compile(r"```(?P<language>[\w+-]*)\n(?P<code>.*?)```", re.DOTALL)
_CHANGE_TITLE_PATTERN = re.compile(r"^\s*#{4,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)


class CodeChatService:
    """Code-specific façade over the audited RAG pipeline."""

    def __init__(
        self,
        db=None,
        *,
        model: str | None = None,
        thinking_mode: ThinkingMode = ThinkingMode.DEFAULT,
    ) -> None:
        self.rag_service = RAGService(
            db=db,
            model=model,
            thinking_mode=thinking_mode,
        )
        self.prompt_budget_service = PromptBudgetService()

    async def answer(
        self,
        payload: CodeChatRequest,
        request_id: str,
        principal: Principal,
    ) -> CodeChatResponse:
        response = await self.rag_service.answer(
            payload,
            request_id=request_id,
            principal=principal,
            chat_type=ChatType.CODE,
            code_context=self._code_context(payload),
            finalize_response=False,
        )
        return self._structured_response(response)

    async def stream_answer(
        self,
        payload: CodeChatRequest,
        request_id: str,
        principal: Principal,
    ) -> AsyncIterator[dict]:
        async for event in self.rag_service.stream_answer(
            payload,
            request_id=request_id,
            principal=principal,
            chat_type=ChatType.CODE,
            code_context=self._code_context(payload),
            finalize_response=False,
        ):
            if event.get("event") == "done" and event.get("response") is not None:
                event = {**event, "response": self._structured_response(event["response"])}
            yield event

    def _code_context(self, payload: CodeChatRequest) -> str:
        if not payload.code:
            return ""
        code, truncated = self.prompt_budget_service.truncate_code(payload.code)
        metadata = []
        if payload.file_name:
            metadata.append(f"File: {payload.file_name}")
        if payload.language:
            metadata.append(f"Language: {payload.language}")
        if payload.line_start is not None:
            end = payload.line_end or payload.line_start
            metadata.append(f"Lines: {payload.line_start}-{end}")
        if truncated:
            metadata.append("Prompt note: code was truncated to the configured safe input budget")
        prefix = "\n".join(metadata)
        return f"{prefix}\n\n{code}" if prefix else code

    def _structured_response(self, response) -> CodeChatResponse:
        sections = self._split_sections(response.answer)
        diagnosis_text = sections.get("diagnosis", "")
        blocks = [
            CodeBlock(language=match.group("language") or None, code=match.group("code").strip())
            for match in _CODE_BLOCK_PATTERN.finditer(response.answer)
            if match.group("code").strip()
        ]
        changes = self._suggested_changes(sections.get("suggested changes", ""))
        usage = get_llm_token_usage()
        payload = response.model_dump()
        payload.update(
            answer=sections.get("answer", response.answer).strip(),
            diagnosis=CodeDiagnosis(
                summary=self._strip_code_blocks(diagnosis_text) or None,
                confidence=self._diagnosis_confidence(diagnosis_text),
            ),
            suggested_changes=changes,
            risks=self._items(sections.get("risks", "")),
            code_blocks=blocks,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
        )
        return CodeChatResponse(**payload)

    def _split_sections(self, answer: str) -> dict[str, str]:
        matches = list(_SECTION_PATTERN.finditer(answer))
        if not matches:
            return {"answer": answer}
        result: dict[str, str] = {}
        for index, match in enumerate(matches):
            name = re.sub(r"\s+", " ", match.group("name").lower())
            end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
            result[name] = answer[match.end():end].strip()
        return result

    def _suggested_changes(self, text: str) -> list[SuggestedCodeChange]:
        titles = list(_CHANGE_TITLE_PATTERN.finditer(text))
        if not titles:
            body = self._strip_code_blocks(text)
            codes = list(_CODE_BLOCK_PATTERN.finditer(text))
            if not body and not codes:
                return []
            return [SuggestedCodeChange(
                title="Suggested change",
                description=body or None,
                code=codes[0].group("code").strip() if codes else None,
                language=codes[0].group("language") or None if codes else None,
            )]
        changes = []
        for index, title in enumerate(titles):
            end = titles[index + 1].start() if index + 1 < len(titles) else len(text)
            body = text[title.end():end].strip()
            code = _CODE_BLOCK_PATTERN.search(body)
            changes.append(SuggestedCodeChange(
                title=title.group("title").strip(),
                description=self._strip_code_blocks(body) or None,
                code=code.group("code").strip() if code else None,
                language=(code.group("language") or None) if code else None,
            ))
        return changes

    def _items(self, text: str) -> list[str]:
        values = [re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s*)", "", line).strip() for line in text.splitlines()]
        return [value for value in values if value]

    def _strip_code_blocks(self, text: str) -> str:
        return _CODE_BLOCK_PATTERN.sub("", text).strip()

    def _diagnosis_confidence(self, text: str) -> str | None:
        match = re.search(r"confidence\s*[:：]\s*(.+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else None
