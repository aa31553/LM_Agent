import time
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ErrorCode, MessageRole, RiskLevel
from app.core.exceptions import APIError
from app.core.security import Principal
from app.rag.citation_builder import CitationBuilder
from app.rag.context_builder import ContextBuilder
from app.rag.prompt_builder import PromptBuilder
from app.rag.query_processor import QueryProcessor
from app.rag.retriever import HybridRetriever
from app.schemas.chat import ChatQueryRequest, ChatQueryResponse
from app.security.prompt_injection_detector import PromptInjectionDetector
from app.services.agent_tool_service import AgentToolService
from app.services.audit_service import AuditService
from app.services.image_context_service import ImageContextService
from app.services.llm_service import LLMService
from app.services.llmwiki_service import LLMWikiService
from app.services.masking_service import MaskingService
from app.services.vector_store_service import RetrievedChunk


class RAGService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db
        self.masking_service = MaskingService(db)
        self.prompt_injection_detector = PromptInjectionDetector()
        self.query_processor = QueryProcessor()
        self.retriever = HybridRetriever(db=db)
        self.context_builder = ContextBuilder()
        self.prompt_builder = PromptBuilder()
        self.llm_service = LLMService()
        self.agent_tool_service = AgentToolService(db=db, llm_service=self.llm_service)
        self.citation_builder = CitationBuilder()
        self.audit_service = AuditService(db)
        self.image_context_service = ImageContextService(db)
        self.llmwiki_service = LLMWikiService(db)

    async def answer(
        self,
        payload: ChatQueryRequest,
        request_id: str,
        principal: Principal,
    ) -> ChatQueryResponse:
        user = self.audit_service.ensure_user(principal)
        session = self.audit_service.ensure_session(
            principal=principal,
            user=user,
            session_id=payload.session_id,
            title_seed=payload.query,
        )
        query_dlp = self.masking_service.scan_and_mask(payload.query, location="query")
        processed_query = self.query_processor.normalize(query_dlp.text)
        user_message = self.audit_service.record_message(
            session=session,
            user_id=user.id,
            role=MessageRole.USER,
            original_content=payload.query,
            masked_content=query_dlp.text,
            final_content=processed_query,
            risk_level=query_dlp.risk_level,
        )
        self.audit_service.record_masking_events(user_message.id, query_dlp, "query")
        if self._block_prompt_injection(payload.query, request_id, user.id, user_message.id):
            raise APIError(ErrorCode.DLP_BLOCKED, "The request contains prompt injection content.", 400)
        if query_dlp.blocked:
            self.audit_service.record_event(
                "dlp_blocked",
                "Query was blocked by DLP.",
                {"request_id": request_id},
                user_id=user.id,
                target_type="chat_message",
                target_id=user_message.id,
                risk_level=query_dlp.risk_level,
            )
            if self.db is not None:
                self.db.commit()
            raise APIError(ErrorCode.DLP_BLOCKED, "The request contains restricted information.", 400)

        requested_top_k = payload.top_k
        chunks = await self.retriever.retrieve(
            query=processed_query,
            knowledge_base_ids=payload.knowledge_base_ids,
            top_k=self._retrieval_candidate_top_k(requested_top_k),
            use_rerank=payload.use_rerank,
            principal=principal,
        )
        wants_images = self._query_wants_images(processed_query)
        chunks = self._filter_image_chunks_for_query(chunks, wants_images)[:requested_top_k]
        llmwiki_context = self.llmwiki_service.build_reference_context(
            query=processed_query,
            knowledge_base_ids=payload.knowledge_base_ids,
            principal=principal,
            max_chars=self._llmwiki_context_char_budget(),
        )

        if not chunks and not llmwiki_context:
            answer = "The available documents do not contain enough information to answer this question."
            self.audit_service.record_message(
                session=session,
                user_id=None,
                role=MessageRole.ASSISTANT,
                original_content=answer,
                masked_content=answer,
                final_content=answer,
                risk_level=RiskLevel.LOW,
            )
            self.audit_service.record_event(
                "query_executed",
                "Query executed without retrievable context.",
                {
                    "request_id": request_id,
                    "knowledge_base_ids": [str(item) for item in payload.knowledge_base_ids],
                    "retrieved_chunks": 0,
                },
                user_id=user.id,
                target_type="chat_message",
                target_id=user_message.id,
                risk_level=RiskLevel.INSUFFICIENT,
            )
            if self.db is not None:
                self.db.commit()
            return ChatQueryResponse(
                request_id=request_id,
                session_id=session.id,
                message_id=user_message.id,
                answer=answer,
                citations=[],
                confidence=RiskLevel.INSUFFICIENT,
                risk_level=RiskLevel.LOW,
                masked_entities=query_dlp.masked_entities,
            )

        if chunks:
            context, used_chunks = self.context_builder.build_with_used_chunks(
                chunks,
                max_tokens=self._retrieval_context_token_budget(),
                max_chars=self._retrieval_context_char_budget(),
            )
        else:
            context, used_chunks = "", []
        if self._block_restricted_context(used_chunks, request_id, user.id, user_message.id):
            raise APIError(ErrorCode.DLP_BLOCKED, "Restricted content cannot be sent to the LLM.", 400)
        used_chunk_ids = {chunk.chunk_id for chunk in used_chunks}
        image_models = (
            self.image_context_service.images_for_chunks(used_chunks)
            if wants_images or self._contains_image_chunks(used_chunks)
            else []
        )
        image_context = self.image_context_service.build_context(
            image_models,
            max_chars=self._image_context_char_budget(),
        )
        self.audit_service.record_retrieval_logs(
            message_id=user_message.id,
            query=processed_query,
            chunks=chunks,
            used_chunk_ids=used_chunk_ids,
        )
        context_dlp = self.masking_service.scan_and_mask(context, location="context")
        image_context_dlp = self.masking_service.scan_and_mask(image_context, location="context")
        llmwiki_context_dlp = self.masking_service.scan_and_mask(llmwiki_context, location="context")
        self.audit_service.record_masking_events(user_message.id, context_dlp, "context")
        self.audit_service.record_masking_events(user_message.id, image_context_dlp, "context")
        self.audit_service.record_masking_events(user_message.id, llmwiki_context_dlp, "context")
        system_prompt, user_prompt = self.prompt_builder.build(
            masked_query=processed_query,
            retrieved_context=context_dlp.text,
            image_context=image_context_dlp.text,
            llmwiki_context=llmwiki_context_dlp.text,
        )
        tool_traces = []
        if payload.use_tools:
            try:
                agent_answer = await self.agent_tool_service.answer_with_tools(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    knowledge_base_ids=payload.knowledge_base_ids,
                    top_k=payload.top_k,
                    use_rerank=payload.use_rerank,
                    principal=principal,
                )
            except Exception as exc:
                self.audit_service.record_completed_llm_call(
                    message_id=user_message.id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    answer="",
                    latency_ms=0,
                    status="failed",
                    error_message=str(exc),
                )
                self.audit_service.record_event(
                    "llm_call_failed",
                    "Agent tool calling failed.",
                    {"error": str(exc)},
                    user_id=user.id,
                    target_type="chat_message",
                    target_id=user_message.id,
                    risk_level=RiskLevel.HIGH,
                )
                if self.db is not None:
                    self.db.commit()
                raise
            answer = agent_answer.answer
            latency_ms = agent_answer.latency_ms
            tool_traces = agent_answer.tool_calls
            self.audit_service.record_completed_llm_call(
                message_id=user_message.id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                answer=answer,
                latency_ms=latency_ms,
            )
        else:
            answer, latency_ms = await self.audit_service.record_llm_call(
                message_id=user_message.id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                operation=lambda: self.llm_service.complete(
                    system_prompt,
                    user_prompt,
                    image_paths=[image.image_path for image in image_models],
                ),
            )
        response_dlp = self.masking_service.scan_and_mask(answer, location="response")
        assistant_message = self.audit_service.record_message(
            session=session,
            user_id=None,
            role=MessageRole.ASSISTANT,
            original_content=answer,
            masked_content=response_dlp.text,
            final_content=response_dlp.text,
            risk_level=response_dlp.risk_level,
        )
        self.audit_service.record_masking_events(user_message.id, response_dlp, "response")
        self.audit_service.record_event(
            "query_executed",
            "Query executed successfully.",
            {
                "request_id": request_id,
                "knowledge_base_ids": [str(item) for item in payload.knowledge_base_ids],
                "retrieved_chunks": len(chunks),
                "context_chunks": len(used_chunks),
                "image_count": len(image_models),
                "llmwiki_context_used": bool(llmwiki_context_dlp.text),
                "tool_call_count": len(tool_traces),
                "tool_calls": [trace.model_dump(mode="json") for trace in tool_traces],
                "assistant_message_id": str(assistant_message.id),
                "llm_latency_ms": latency_ms,
            },
            user_id=user.id,
            target_type="chat_message",
            target_id=user_message.id,
            risk_level=response_dlp.risk_level,
        )
        if response_dlp.blocked:
            self.audit_service.record_event(
                "dlp_blocked",
                "Response contained restricted information and was masked.",
                {"request_id": request_id},
                user_id=user.id,
                target_type="chat_message",
                target_id=user_message.id,
                risk_level=response_dlp.risk_level,
            )

        if self.db is not None:
            self.db.commit()

        return ChatQueryResponse(
            request_id=request_id,
            session_id=session.id,
            message_id=user_message.id,
            answer=response_dlp.text,
            citations=self.citation_builder.from_chunks(used_chunks),
            images=self.image_context_service.to_references(image_models),
            confidence=RiskLevel.MEDIUM,
            risk_level=response_dlp.risk_level,
            masked_entities=(
                query_dlp.masked_entities
                + context_dlp.masked_entities
                + image_context_dlp.masked_entities
                + llmwiki_context_dlp.masked_entities
                + response_dlp.masked_entities
            ),
            tool_calls=tool_traces,
        )

    async def stream_answer(
        self,
        payload: ChatQueryRequest,
        request_id: str,
        principal: Principal,
    ) -> AsyncIterator[dict[str, Any]]:
        user = self.audit_service.ensure_user(principal)
        session = self.audit_service.ensure_session(
            principal=principal,
            user=user,
            session_id=payload.session_id,
            title_seed=payload.query,
        )
        query_dlp = self.masking_service.scan_and_mask(payload.query, location="query")
        processed_query = self.query_processor.normalize(query_dlp.text)
        user_message = self.audit_service.record_message(
            session=session,
            user_id=user.id,
            role=MessageRole.USER,
            original_content=payload.query,
            masked_content=query_dlp.text,
            final_content=processed_query,
            risk_level=query_dlp.risk_level,
        )
        self.audit_service.record_masking_events(user_message.id, query_dlp, "query")
        yield {
            "event": "start",
            "request_id": request_id,
            "session_id": str(session.id),
            "message_id": str(user_message.id),
        }
        if self._block_prompt_injection(payload.query, request_id, user.id, user_message.id):
            raise APIError(ErrorCode.DLP_BLOCKED, "The request contains prompt injection content.", 400)

        if query_dlp.blocked:
            self.audit_service.record_event(
                "dlp_blocked",
                "Query was blocked by DLP.",
                {"request_id": request_id},
                user_id=user.id,
                target_type="chat_message",
                target_id=user_message.id,
                risk_level=query_dlp.risk_level,
            )
            if self.db is not None:
                self.db.commit()
            raise APIError(ErrorCode.DLP_BLOCKED, "The request contains restricted information.", 400)

        requested_top_k = payload.top_k
        chunks = await self.retriever.retrieve(
            query=processed_query,
            knowledge_base_ids=payload.knowledge_base_ids,
            top_k=self._retrieval_candidate_top_k(requested_top_k),
            use_rerank=payload.use_rerank,
            principal=principal,
        )
        wants_images = self._query_wants_images(processed_query)
        chunks = self._filter_image_chunks_for_query(chunks, wants_images)[:requested_top_k]
        llmwiki_context = self.llmwiki_service.build_reference_context(
            query=processed_query,
            knowledge_base_ids=payload.knowledge_base_ids,
            principal=principal,
            max_chars=self._llmwiki_context_char_budget(),
        )
        if not chunks and not llmwiki_context:
            answer = "The available documents do not contain enough information to answer this question."
            self.audit_service.record_message(
                session=session,
                user_id=None,
                role=MessageRole.ASSISTANT,
                original_content=answer,
                masked_content=answer,
                final_content=answer,
                risk_level=RiskLevel.LOW,
            )
            self.audit_service.record_event(
                "query_executed",
                "Query executed without retrievable context.",
                {
                    "request_id": request_id,
                    "knowledge_base_ids": [str(item) for item in payload.knowledge_base_ids],
                    "retrieved_chunks": 0,
                },
                user_id=user.id,
                target_type="chat_message",
                target_id=user_message.id,
                risk_level=RiskLevel.INSUFFICIENT,
            )
            if self.db is not None:
                self.db.commit()
            response = ChatQueryResponse(
                request_id=request_id,
                session_id=session.id,
                message_id=user_message.id,
                answer=answer,
                citations=[],
                confidence=RiskLevel.INSUFFICIENT,
                risk_level=RiskLevel.LOW,
                masked_entities=query_dlp.masked_entities,
            )
            yield {"event": "delta", "text": answer}
            yield {"event": "done", "response": response}
            return

        if chunks:
            context, used_chunks = self.context_builder.build_with_used_chunks(
                chunks,
                max_tokens=self._retrieval_context_token_budget(),
                max_chars=self._retrieval_context_char_budget(),
            )
        else:
            context, used_chunks = "", []
        if self._block_restricted_context(used_chunks, request_id, user.id, user_message.id):
            raise APIError(ErrorCode.DLP_BLOCKED, "Restricted content cannot be sent to the LLM.", 400)
        used_chunk_ids = {chunk.chunk_id for chunk in used_chunks}
        image_models = (
            self.image_context_service.images_for_chunks(used_chunks)
            if wants_images or self._contains_image_chunks(used_chunks)
            else []
        )
        image_context = self.image_context_service.build_context(
            image_models,
            max_chars=self._image_context_char_budget(),
        )
        self.audit_service.record_retrieval_logs(
            message_id=user_message.id,
            query=processed_query,
            chunks=chunks,
            used_chunk_ids=used_chunk_ids,
        )
        context_dlp = self.masking_service.scan_and_mask(context, location="context")
        image_context_dlp = self.masking_service.scan_and_mask(image_context, location="context")
        llmwiki_context_dlp = self.masking_service.scan_and_mask(llmwiki_context, location="context")
        self.audit_service.record_masking_events(user_message.id, context_dlp, "context")
        self.audit_service.record_masking_events(user_message.id, image_context_dlp, "context")
        self.audit_service.record_masking_events(user_message.id, llmwiki_context_dlp, "context")
        system_prompt, user_prompt = self.prompt_builder.build(
            masked_query=processed_query,
            retrieved_context=context_dlp.text,
            image_context=image_context_dlp.text,
            llmwiki_context=llmwiki_context_dlp.text,
        )

        answer_parts: list[str] = []
        tool_traces = []
        if payload.use_tools:
            try:
                agent_answer = await self.agent_tool_service.answer_with_tools(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    knowledge_base_ids=payload.knowledge_base_ids,
                    top_k=payload.top_k,
                    use_rerank=payload.use_rerank,
                    principal=principal,
                )
            except Exception as exc:
                self.audit_service.record_completed_llm_call(
                    message_id=user_message.id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    answer="",
                    latency_ms=0,
                    status="failed",
                    error_message=str(exc),
                )
                self.audit_service.record_event(
                    "llm_call_failed",
                    "Agent tool calling failed.",
                    {"error": str(exc)},
                    user_id=user.id,
                    target_type="chat_message",
                    target_id=user_message.id,
                    risk_level=RiskLevel.HIGH,
                )
                if self.db is not None:
                    self.db.commit()
                raise
            answer = agent_answer.answer
            latency_ms = agent_answer.latency_ms
            tool_traces = agent_answer.tool_calls
            if answer:
                yield {"event": "delta", "text": answer}
            self.audit_service.record_completed_llm_call(
                message_id=user_message.id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                answer=answer,
                latency_ms=latency_ms,
            )
        else:
            started = time.perf_counter()
            try:
                async for delta in self.llm_service.stream_complete(
                    system_prompt,
                    user_prompt,
                    image_paths=[image.image_path for image in image_models],
                ):
                    answer_parts.append(delta)
                    yield {"event": "delta", "text": delta}
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                self.audit_service.record_completed_llm_call(
                    message_id=user_message.id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    answer="".join(answer_parts),
                    latency_ms=latency_ms,
                    status="failed",
                    error_message=str(exc),
                )
                self.audit_service.record_event(
                    "llm_call_failed",
                    "LLM streaming call failed.",
                    {"error": str(exc)},
                    user_id=user.id,
                    target_type="chat_message",
                    target_id=user_message.id,
                    risk_level=RiskLevel.HIGH,
                )
                if self.db is not None:
                    self.db.commit()
                raise

            answer = "".join(answer_parts)
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.audit_service.record_completed_llm_call(
                message_id=user_message.id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                answer=answer,
                latency_ms=latency_ms,
            )
        response_dlp = self.masking_service.scan_and_mask(answer, location="response")
        assistant_message = self.audit_service.record_message(
            session=session,
            user_id=None,
            role=MessageRole.ASSISTANT,
            original_content=answer,
            masked_content=response_dlp.text,
            final_content=response_dlp.text,
            risk_level=response_dlp.risk_level,
        )
        self.audit_service.record_masking_events(user_message.id, response_dlp, "response")
        self.audit_service.record_event(
            "query_executed",
            "Streaming query executed successfully.",
            {
                "request_id": request_id,
                "knowledge_base_ids": [str(item) for item in payload.knowledge_base_ids],
                "retrieved_chunks": len(chunks),
                "context_chunks": len(used_chunks),
                "image_count": len(image_models),
                "llmwiki_context_used": bool(llmwiki_context_dlp.text),
                "tool_call_count": len(tool_traces),
                "tool_calls": [trace.model_dump(mode="json") for trace in tool_traces],
                "assistant_message_id": str(assistant_message.id),
                "llm_latency_ms": latency_ms,
            },
            user_id=user.id,
            target_type="chat_message",
            target_id=user_message.id,
            risk_level=response_dlp.risk_level,
        )
        if self.db is not None:
            self.db.commit()

        response = ChatQueryResponse(
            request_id=request_id,
            session_id=session.id,
            message_id=user_message.id,
            answer=response_dlp.text,
            citations=self.citation_builder.from_chunks(used_chunks),
            images=self.image_context_service.to_references(image_models),
            confidence=RiskLevel.MEDIUM,
            risk_level=response_dlp.risk_level,
            masked_entities=(
                query_dlp.masked_entities
                + context_dlp.masked_entities
                + image_context_dlp.masked_entities
                + llmwiki_context_dlp.masked_entities
                + response_dlp.masked_entities
            ),
            tool_calls=tool_traces,
        )
        yield {"event": "done", "response": response}

    def _retrieval_context_token_budget(self) -> int:
        reserved_tokens = settings.llm_max_tokens + 900
        return max(1000, settings.context_max_tokens - reserved_tokens)

    def _retrieval_context_char_budget(self) -> int:
        return max(4000, settings.context_max_chars - self._image_context_char_budget() - 3000)

    def _image_context_char_budget(self) -> int:
        return min(6000, max(2000, settings.context_max_chars // 5))

    def _llmwiki_context_char_budget(self) -> int:
        return min(7000, max(2500, settings.context_max_chars // 4))

    def _retrieval_candidate_top_k(self, requested_top_k: int) -> int:
        return max(requested_top_k, min(requested_top_k * 3, requested_top_k + 20))

    def _query_wants_images(self, query: str) -> bool:
        lowered = query.lower()
        image_terms = (
            "image",
            "figure",
            "fig.",
            "fig ",
            "chart",
            "diagram",
            "graph",
            "table",
            "圖片",
            "圖",
            "圖表",
            "表格",
            "mechanical abuse",
            "thermal abuse",
            "thermal runaway",
            "熱失控",
            "機械濫用",
            "熱濫用",
        )
        return any(term in lowered for term in image_terms)

    def _filter_image_chunks_for_query(
        self,
        chunks: list[RetrievedChunk],
        wants_images: bool,
    ) -> list[RetrievedChunk]:
        if wants_images:
            return chunks
        text_chunks = [
            chunk for chunk in chunks if (chunk.metadata or {}).get("source_type") != "pdf_image"
        ]
        return text_chunks or chunks

    def _contains_image_chunks(self, chunks: list[RetrievedChunk]) -> bool:
        return any((chunk.metadata or {}).get("source_type") == "pdf_image" for chunk in chunks)

    def _block_prompt_injection(
        self,
        query: str,
        request_id: str,
        user_id,
        message_id,
    ) -> bool:
        if not self.prompt_injection_detector.is_suspicious(query):
            return False
        self.audit_service.record_event(
            "prompt_injection_detected",
            "Query matched prompt injection controls.",
            {"request_id": request_id},
            user_id=user_id,
            target_type="chat_message",
            target_id=message_id,
            risk_level=RiskLevel.HIGH,
        )
        self.audit_service.record_event(
            "dlp_blocked",
            "Query was blocked by prompt injection controls.",
            {"request_id": request_id},
            user_id=user_id,
            target_type="chat_message",
            target_id=message_id,
            risk_level=RiskLevel.HIGH,
        )
        if self.db is not None:
            self.db.commit()
        return True

    def _block_restricted_context(
        self,
        used_chunks: list[RetrievedChunk],
        request_id: str,
        user_id,
        message_id,
    ) -> bool:
        restricted_chunks = [
            chunk
            for chunk in used_chunks
            if (chunk.metadata or {}).get("confidential_level") == "restricted"
            or (chunk.metadata or {}).get("send_to_llm") is False
        ]
        if not restricted_chunks:
            return False
        self.audit_service.record_event(
            "dlp_blocked",
            "Restricted context was blocked before LLM call.",
            {
                "request_id": request_id,
                "blocked_chunk_ids": [str(chunk.chunk_id) for chunk in restricted_chunks],
            },
            user_id=user_id,
            target_type="chat_message",
            target_id=message_id,
            risk_level=RiskLevel.HIGH,
        )
        if self.db is not None:
            self.db.commit()
        return True
