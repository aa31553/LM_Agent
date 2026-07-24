import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.constants import DocumentStatus
from app.core.security import Principal
from app.models.knowledge_base import KnowledgeBase
from app.schemas.llmwiki import (
    LLMWikiEvidence,
    LLMWikiGraph,
    LLMWikiGraphEdge,
    LLMWikiGraphNode,
    LLMWikiIndexItem,
    LLMWikiLintIssue,
    LLMWikiLintResponse,
    LLMWikiLink,
    LLMWikiOperationLogItem,
    LLMWikiTopicCandidate,
    LLMWikiTopicPage,
)
from app.services.permission_service import PermissionService
from app.services.vector_store_service import VectorStoreService
from app.services.llm_service import LLMService


_TERM_PATTERN = re.compile(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]{1,}", re.UNICODE)
_LATIN_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_CJK_RUN_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,8}")
_SENTENCE_PATTERN = re.compile(r"[^。！？!?；;\n\r]{12,}[。！？!?；;]?", re.UNICODE)
_SCHEMA_VERSION = 2
_STOPWORDS = {
    "a",
    "an",
    "and",
    "about",
    "after",
    "also",
    "because",
    "before",
    "between",
    "could",
    "from",
    "for",
    "has",
    "have",
    "into",
    "more",
    "only",
    "other",
    "should",
    "than",
    "that",
    "the",
    "their",
    "there",
    "these",
    "this",
    "those",
    "through",
    "with",
    "within",
    "would",
    "are",
    "was",
    "were",
    "been",
    "being",
    "can",
    "may",
    "might",
    "must",
    "shall",
    "will",
    "org",
    "com",
    "net",
    "doi",
    "http",
    "https",
    "www",
    "journal",
    "vol",
    "pages",
    "page",
    "article",
    "copyright",
    "elsevier",
    "springer",
    "wiley",
    "science",
    "direct",
    "et",
    "al",
    "使用",
    "可以",
    "以及",
    "資料",
    "文件",
    "目前",
    "相關",
    "系統",
}

_DOMAIN_SUFFIXES = {
    "com",
    "org",
    "net",
    "edu",
    "gov",
    "io",
    "ai",
    "cn",
    "tw",
    "uk",
    "de",
    "jp",
}

_NOISE_TERMS = _STOPWORDS | _DOMAIN_SUFFIXES | {
    "10",
    "1016",
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "fig",
    "figure",
    "table",
    "references",
    "reference",
    "abstract",
    "introduction",
}

_ALLOWED_SINGLE_WORD_TOPICS = {
    "rag",
    "llmwiki",
    "llm",
    "ocr",
    "dlp",
    "pgvector",
}


_SENTENCE_PATTERN = re.compile(r"[^。！？!?；;\n\r]{12,}[。！？!?；;]?", re.UNICODE)
_STOPWORDS.update(
    {
        "使用",
        "可以",
        "以及",
        "資料",
        "文件",
        "目前",
        "相關",
        "系統",
        "研究",
        "結果",
        "方法",
        "安全",
        "運輸",
    }
)


@dataclass(frozen=True)
class _WikiChunk:
    chunk_id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    content: str
    document_title: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    confidential_level: str
    source_type: str | None
    metadata: dict
    created_at: datetime | None
    score: float


@dataclass(frozen=True)
class _TopicSignal:
    topic: str
    score: float
    chunks: list[_WikiChunk]
    aliases: list[str]
    page_type: str = "concept"


@dataclass(frozen=True)
class _TopicReview:
    decision: str
    canonical_topic: str
    page_type: str
    aliases: list[str]
    quality_score: float
    confidence: float
    rejection_reason: str = ""
    related_topic_hints: list[str] | None = None


class LLMWikiService:
    _schema_ready = False

    def __init__(self, db: Session | None = None, llm_service: LLMService | None = None) -> None:
        self.db = db
        self.llm_service = llm_service or LLMService()
        if self.db is not None and not LLMWikiService._schema_ready:
            self._ensure_schema()
            LLMWikiService._schema_ready = True

    def search_topics(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        limit: int,
        principal: Principal,
    ) -> list[LLMWikiTopicCandidate]:
        compiled = self._search_compiled_topics(query, knowledge_base_ids, limit, principal)
        if compiled:
            return compiled

        registry = self._search_registry_topics(query, knowledge_base_ids, limit, principal)
        if registry:
            return registry

        chunks = self._load_chunks(
            query=query,
            knowledge_base_ids=knowledge_base_ids,
            limit=max(limit * 12, 80),
            principal=principal,
        )
        signals = self._topic_signals_from_chunks(chunks, limit=max(limit * 3, limit))
        if query.strip() and chunks and not self._is_noise_topic(query):
            signals.insert(0, self._signal_for_requested_topic(query, chunks))
        for signal in signals:
            self._upsert_topic_review(
                signal=signal,
                knowledge_base_id=signal.chunks[0].knowledge_base_id if signal.chunks else knowledge_base_ids[0],
                review=self._deterministic_review(signal),
                reviewer="deterministic",
                commit=False,
            )
        if self.db is not None and signals:
            self.db.commit()
        return self._search_registry_topics(query, knowledge_base_ids, limit, principal)

    async def search_topics_reviewed(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        limit: int,
        principal: Principal,
    ) -> list[LLMWikiTopicCandidate]:
        compiled = self._search_compiled_topics(query, knowledge_base_ids, limit, principal)
        if compiled:
            return compiled

        registry = self._search_registry_topics(query, knowledge_base_ids, limit, principal)
        if registry:
            return registry

        chunks = self._load_chunks(
            query=query,
            knowledge_base_ids=knowledge_base_ids,
            limit=max(limit * 12, 80),
            principal=principal,
        )
        signals = self._topic_signals_from_chunks(chunks, limit=max(limit * 4, 12))
        if query.strip() and chunks and not self._is_noise_topic(query):
            signals.insert(0, self._signal_for_requested_topic(query, chunks))
        for signal in signals:
            review = await self._llm_review_signal(signal)
            self._upsert_topic_review(
                signal=signal,
                knowledge_base_id=signal.chunks[0].knowledge_base_id,
                review=review,
                reviewer="llm",
                commit=False,
            )
        if self.db is not None:
            self.db.commit()
        return self._search_registry_topics(query, knowledge_base_ids, limit, principal)

    def discover_topic_signals(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        limit: int,
        principal: Principal,
    ) -> list[_TopicSignal]:
        chunks = self._load_chunks(
            query=query,
            knowledge_base_ids=knowledge_base_ids,
            limit=max(limit * 12, 80),
            principal=principal,
        )
        return self._topic_signals_from_chunks(chunks, limit=max(limit * 3, limit))

    def build_topic_page(
        self,
        topic: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        include_graph: bool,
        principal: Principal,
        prefer_compiled: bool = True,
    ) -> LLMWikiTopicPage:
        if prefer_compiled:
            compiled = self._load_compiled_page(topic, knowledge_base_ids, include_graph, principal)
            if compiled is not None:
                return compiled

        chunks = self._load_chunks(
            query=topic,
            knowledge_base_ids=knowledge_base_ids,
            limit=top_k,
            principal=principal,
        )
        return self._assemble_topic_page(
            topic=topic,
            chunks=chunks,
            include_graph=include_graph,
            compiled_page_id=None,
            compiled_slug=None,
            last_compiled_at=None,
        )

    def compile_topic(
        self,
        topic: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        include_graph: bool,
        principal: Principal,
    ) -> tuple[LLMWikiTopicPage, str]:
        if self.db is None:
            page = self.build_topic_page(
                topic=topic,
                knowledge_base_ids=knowledge_base_ids,
                top_k=top_k,
                include_graph=include_graph,
                principal=principal,
                prefer_compiled=False,
            )
            return page, "preview"
        if not knowledge_base_ids:
            page = self._assemble_topic_page(topic, [], include_graph, None, None, None)
            return page, "skipped"

        chunks = self._load_chunks(
            query=topic,
            knowledge_base_ids=knowledge_base_ids,
            limit=top_k,
            principal=principal,
        )
        topic_row = self._ensure_topic_review(
            topic=topic,
            chunks=chunks,
            knowledge_base_id=knowledge_base_ids[0],
            reviewer="deterministic",
        )
        if topic_row and topic_row["status"] == "rejected":
            page = self._assemble_topic_page(
                topic=topic,
                chunks=[],
                include_graph=include_graph,
                compiled_page_id=None,
                compiled_slug=None,
                last_compiled_at=None,
                topic_row=topic_row,
            )
            return page, "rejected"
        canonical_topic = topic_row["canonical_topic"] if topic_row else topic
        page = self._assemble_topic_page(
            topic=canonical_topic,
            chunks=chunks,
            include_graph=include_graph,
            compiled_page_id=None,
            compiled_slug=None,
            last_compiled_at=None,
            topic_row=topic_row,
        )
        primary_kb_id = knowledge_base_ids[0]
        slug = self._slugify(page.topic)
        fingerprint = self._fingerprint(chunks)
        now = datetime.utcnow()
        linked_topics = [link.model_dump(mode="json") for link in page.linked_topics]
        metadata = {
            "aliases": page.aliases,
            "contradictions": page.contradictions,
            "maintenance_notes": page.maintenance_notes,
            "source_knowledge_base_ids": [str(item) for item in knowledge_base_ids],
            "fingerprint": fingerprint,
            "topic_id": str(topic_row["id"]) if topic_row else None,
            "page_type": page.page_type,
            "topic_quality_score": page.topic_quality_score,
            "topic_review_model": (topic_row.get("metadata") or {}).get("reviewer") if topic_row else "deterministic",
            "topic_reviewed_at": self._iso(topic_row["updated_at"]) if topic_row else self._iso(now),
            "schema_version": _SCHEMA_VERSION,
        }
        existing = self.db.execute(
            text(
                """
                SELECT id FROM llmwiki_pages
                WHERE knowledge_base_id = :knowledge_base_id AND slug = :slug
                """
            ),
            {"knowledge_base_id": str(primary_kb_id), "slug": slug},
        ).mappings().first()
        operation = "updated" if existing else "created"
        if existing:
            page_id = existing["id"]
            self.db.execute(
                text(
                    """
                    UPDATE llmwiki_pages
                    SET topic = :topic,
                        summary = :summary,
                        content_markdown = :content_markdown,
                        key_points = CAST(:key_points AS jsonb),
                        linked_topics = CAST(:linked_topics AS jsonb),
                        source_document_count = :source_document_count,
                        source_chunk_count = :source_chunk_count,
                        fingerprint = :fingerprint,
                        metadata = CAST(:metadata AS jsonb),
                        updated_at = :updated_at
                    WHERE id = :page_id
                    """
                ),
                {
                    "page_id": str(page_id),
                    "topic": page.topic,
                    "summary": page.summary,
                    "content_markdown": page.content_markdown or "",
                    "key_points": json.dumps(page.key_points, ensure_ascii=False),
                    "linked_topics": json.dumps(linked_topics, ensure_ascii=False),
                    "source_document_count": page.source_document_count,
                    "source_chunk_count": page.source_chunk_count,
                    "fingerprint": fingerprint,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "updated_at": now,
                },
            )
        else:
            page_id = self.db.execute(
                text(
                    """
                    INSERT INTO llmwiki_pages (
                        knowledge_base_id, topic, slug, summary, content_markdown,
                        key_points, linked_topics, source_document_count, source_chunk_count,
                        fingerprint, metadata, created_at, updated_at
                    )
                    VALUES (
                        :knowledge_base_id, :topic, :slug, :summary, :content_markdown,
                        CAST(:key_points AS jsonb), CAST(:linked_topics AS jsonb),
                        :source_document_count, :source_chunk_count, :fingerprint,
                        CAST(:metadata AS jsonb), :created_at, :updated_at
                    )
                    RETURNING id
                    """
                ),
                {
                    "knowledge_base_id": str(primary_kb_id),
                    "topic": page.topic,
                    "slug": slug,
                    "summary": page.summary,
                    "content_markdown": page.content_markdown or "",
                    "key_points": json.dumps(page.key_points, ensure_ascii=False),
                    "linked_topics": json.dumps(linked_topics, ensure_ascii=False),
                    "source_document_count": page.source_document_count,
                    "source_chunk_count": page.source_chunk_count,
                    "fingerprint": fingerprint,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                },
            ).scalar_one()

        self.db.execute(
            text("DELETE FROM llmwiki_page_evidence WHERE page_id = :page_id"),
            {"page_id": str(page_id)},
        )
        for rank, evidence in enumerate(page.evidence, start=1):
            self.db.execute(
                text(
                    """
                    INSERT INTO llmwiki_page_evidence (
                        page_id, chunk_id, document_id, knowledge_base_id,
                        rank, score, snippet, metadata
                    )
                    VALUES (
                        :page_id, :chunk_id, :document_id, :knowledge_base_id,
                        :rank, :score, :snippet, CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "page_id": str(page_id),
                    "chunk_id": str(evidence.chunk_id),
                    "document_id": str(evidence.document_id),
                    "knowledge_base_id": str(evidence.knowledge_base_id),
                    "rank": rank,
                    "score": evidence.score,
                    "snippet": evidence.snippet,
                    "metadata": json.dumps(
                        {
                            "document_title": evidence.document_title,
                            "section_title": evidence.section_title,
                            "page_start": evidence.page_start,
                            "page_end": evidence.page_end,
                            "confidential_level": evidence.confidential_level,
                            "source_type": evidence.source_type,
                        },
                        ensure_ascii=False,
                    ),
                },
            )
        self._log_operation(
            "compile",
            page.topic,
            f"{operation.title()} LLMWiki page '{page.topic}'.",
            {
                "page_id": str(page_id),
                "knowledge_base_id": str(primary_kb_id),
                "source_chunk_count": page.source_chunk_count,
                "source_document_count": page.source_document_count,
                "topic_id": str(topic_row["id"]) if topic_row else None,
                "schema_version": _SCHEMA_VERSION,
            },
        )
        if topic_row:
            self.db.execute(
                text(
                    """
                    UPDATE llmwiki_topics
                    SET status = 'compiled', updated_at = :updated_at
                    WHERE id = :topic_id AND status = 'approved'
                    """
                ),
                {"topic_id": str(topic_row["id"]), "updated_at": now},
            )
        self.db.commit()

        compiled = self._load_compiled_page(page.topic, [primary_kb_id], include_graph, principal)
        return compiled or page, operation

    async def compile_topic_reviewed(
        self,
        topic: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        include_graph: bool,
        principal: Principal,
    ) -> tuple[LLMWikiTopicPage, str]:
        if self.db is None:
            return self.compile_topic(topic, knowledge_base_ids, top_k, include_graph, principal)
        if not knowledge_base_ids:
            page = self._assemble_topic_page(topic, [], include_graph, None, None, None)
            return page, "skipped"

        chunks = self._load_chunks(topic, knowledge_base_ids, top_k, principal)
        topic_row = await self._ensure_topic_review_async(
            topic=topic,
            chunks=chunks,
            knowledge_base_id=knowledge_base_ids[0],
        )
        if topic_row and topic_row["status"] == "rejected":
            page = self._assemble_topic_page(
                topic=topic,
                chunks=[],
                include_graph=include_graph,
                compiled_page_id=None,
                compiled_slug=None,
                last_compiled_at=None,
                topic_row=topic_row,
            )
            self._log_operation(
                "compile_rejected",
                topic,
                f"Rejected LLMWiki topic '{topic}' during topic review.",
                {
                    "knowledge_base_id": str(knowledge_base_ids[0]),
                    "topic_id": str(topic_row["id"]),
                    "rejection_reason": topic_row["rejection_reason"],
                },
                commit=True,
            )
            return page, "rejected"
        return self.compile_topic(
            topic=topic_row["canonical_topic"] if topic_row else topic,
            knowledge_base_ids=knowledge_base_ids,
            top_k=top_k,
            include_graph=include_graph,
            principal=principal,
        )

    def build_topic_graph(
        self,
        topic: str,
        knowledge_base_ids: list[UUID],
        top_k: int,
        principal: Principal,
    ) -> LLMWikiGraph:
        compiled = self._load_compiled_page(topic, knowledge_base_ids, True, principal)
        if compiled and compiled.graph:
            return compiled.graph
        chunks = self._load_chunks(
            query=topic,
            knowledge_base_ids=knowledge_base_ids,
            limit=top_k,
            principal=principal,
        )
        return self._graph(topic, chunks, self._linked_topics(topic, chunks))

    def list_index(
        self,
        knowledge_base_ids: list[UUID],
        principal: Principal,
    ) -> list[LLMWikiIndexItem]:
        if self.db is None or not knowledge_base_ids:
            return []
        rows = self.db.execute(
            text(
                """
                SELECT id, knowledge_base_id, topic, slug, summary, linked_topics,
                       source_document_count, source_chunk_count, fingerprint, updated_at
                FROM llmwiki_pages
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                ORDER BY updated_at DESC, topic ASC
                """
            ),
            {"knowledge_base_ids": [str(item) for item in knowledge_base_ids]},
        ).mappings()
        items: list[LLMWikiIndexItem] = []
        for row in rows:
            if not self._can_access_knowledge_base(principal, row["knowledge_base_id"]):
                continue
            current = self._fingerprint(
                self._load_chunks(
                    query=row["topic"],
                    knowledge_base_ids=[row["knowledge_base_id"]],
                    limit=50,
                    principal=principal,
                )
            )
            linked_topics = [
                link.get("topic", "")
                for link in self._json_list(row["linked_topics"])
                if link.get("topic")
            ]
            items.append(
                LLMWikiIndexItem(
                    page_id=row["id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    topic=row["topic"],
                    slug=row["slug"],
                    summary=row["summary"],
                    linked_topics=linked_topics,
                    source_document_count=int(row["source_document_count"] or 0),
                    source_chunk_count=int(row["source_chunk_count"] or 0),
                    stale=current != row["fingerprint"],
                    updated_at=self._iso(row["updated_at"]),
                )
            )
        return items

    def lint(
        self,
        knowledge_base_ids: list[UUID],
        principal: Principal,
    ) -> LLMWikiLintResponse:
        if self.db is None or not knowledge_base_ids:
            return LLMWikiLintResponse(checked_pages=0)
        index_items = self.list_index(knowledge_base_ids, principal)
        issues: list[LLMWikiLintIssue] = []
        compiled_topics = {item.topic.lower() for item in index_items}
        inbound_counts: Counter[str] = Counter()
        for item in index_items:
            for linked in item.linked_topics:
                inbound_counts[linked.lower()] += 1
            if item.source_chunk_count == 0:
                issues.append(
                    LLMWikiLintIssue(
                        code="missing_evidence",
                        severity="high",
                        message="Compiled page has no source evidence.",
                        topic=item.topic,
                        page_id=item.page_id,
                    )
                )
            if item.stale:
                issues.append(
                    LLMWikiLintIssue(
                        code="stale_page",
                        severity="medium",
                        message="Indexed chunks changed after this page was compiled.",
                        topic=item.topic,
                        page_id=item.page_id,
                    )
                )
            if not item.linked_topics and item.source_chunk_count > 1:
                issues.append(
                    LLMWikiLintIssue(
                        code="missing_cross_reference",
                        severity="low",
                        message="Page has evidence but no related topic links.",
                        topic=item.topic,
                        page_id=item.page_id,
                    )
                )
            if inbound_counts[item.topic.lower()] == 0 and len(index_items) > 1:
                issues.append(
                    LLMWikiLintIssue(
                        code="orphan_page",
                        severity="low",
                        message="No other compiled page links to this topic.",
                        topic=item.topic,
                        page_id=item.page_id,
                    )
                )

        topic_counts = Counter()
        rows = self.db.execute(
            text(
                """
                SELECT id, canonical_topic, quality_score, status, source_chunk_count
                FROM llmwiki_topics
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                ORDER BY quality_score DESC, updated_at DESC
                LIMIT 40
                """
            ),
            {"knowledge_base_ids": [str(item) for item in knowledge_base_ids]},
        ).mappings()
        suggestions: list[str] = []
        for row in rows:
            topic_lower = row["canonical_topic"].lower()
            if row["status"] == "rejected":
                continue
            if self._is_noise_topic(row["canonical_topic"]):
                issues.append(
                    LLMWikiLintIssue(
                        code="rejected_topic_link",
                        severity="high",
                        message="Topic registry contains a noise topic that should be rejected.",
                        topic=row["canonical_topic"],
                    )
                )
                continue
            if row["status"] == "candidate":
                issues.append(
                    LLMWikiLintIssue(
                        code="unreviewed_topic",
                        severity="medium",
                        message="Topic has not completed LLM review.",
                        topic=row["canonical_topic"],
                    )
                )
            if float(row["quality_score"] or 0.0) < 0.35:
                issues.append(
                    LLMWikiLintIssue(
                        code="low_quality_topic",
                        severity="medium",
                        message="Topic quality score is below the compilation threshold.",
                        topic=row["canonical_topic"],
                    )
                )
            if topic_lower not in compiled_topics and row["status"] in {"approved", "compiled"}:
                topic_counts[row["canonical_topic"]] += int(row["source_chunk_count"] or 1)
        suggestions = [
            term
            for term, _ in topic_counts.most_common(8)
            if not self._is_noise_topic(term)
        ]
        legacy_rows = self.db.execute(
            text(
                """
                SELECT id, topic
                FROM llmwiki_pages
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                  AND COALESCE((metadata->>'schema_version')::int, 1) < :schema_version
                LIMIT 20
                """
            ),
            {
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "schema_version": _SCHEMA_VERSION,
            },
        ).mappings()
        for row in legacy_rows:
            issues.append(
                LLMWikiLintIssue(
                    code="legacy_schema_page",
                    severity="high",
                    message="Compiled page uses an older LLMWiki schema and should be reset/recompiled.",
                    topic=row["topic"],
                    page_id=row["id"],
                )
            )
        response = LLMWikiLintResponse(
            checked_pages=len(index_items),
            issues=issues,
            suggested_topics=suggestions,
        )
        self._log_operation(
            "lint",
            None,
            f"Checked {len(index_items)} LLMWiki pages and found {len(issues)} issues.",
            {
                "issue_count": len(issues),
                "suggested_topics": suggestions,
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "knowledge_base_id": str(knowledge_base_ids[0]) if knowledge_base_ids else "",
            },
            commit=True,
        )
        return response

    def list_operations(self, limit: int, principal: Principal) -> list[LLMWikiOperationLogItem]:
        if self.db is None:
            return []
        rows = self.db.execute(
            text(
                """
                SELECT id, operation, topic, message, metadata, created_at
                FROM llmwiki_operation_logs
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings()
        if "admin" not in principal.roles:
            return []
        return [
            LLMWikiOperationLogItem(
                operation_id=row["id"],
                operation=row["operation"],
                topic=row["topic"],
                message=row["message"],
                metadata=row["metadata"] or {},
                created_at=self._iso(row["created_at"]),
            )
            for row in rows
        ]

    def reset_knowledge_base(self, knowledge_base_id: UUID) -> None:
        if self.db is None:
            return
        self.db.execute(
            text(
                """
                DELETE FROM llmwiki_operation_logs
                WHERE metadata->>'knowledge_base_id' = :knowledge_base_id
                """
            ),
            {"knowledge_base_id": str(knowledge_base_id)},
        )
        self.db.execute(
            text("DELETE FROM llmwiki_topics WHERE knowledge_base_id = :knowledge_base_id"),
            {"knowledge_base_id": str(knowledge_base_id)},
        )
        self.db.execute(
            text("DELETE FROM llmwiki_pages WHERE knowledge_base_id = :knowledge_base_id"),
            {"knowledge_base_id": str(knowledge_base_id)},
        )
        self.db.commit()

    async def discover_and_review_topics(
        self,
        knowledge_base_id: UUID,
        limit: int,
        principal: Principal,
        query: str = "",
    ) -> list[LLMWikiTopicCandidate]:
        signals = self.discover_topic_signals(query, [knowledge_base_id], limit, principal)
        for signal in signals:
            review = await self._llm_review_signal(signal)
            self._upsert_topic_review(signal, knowledge_base_id, review, "llm", commit=False)
        if self.db is not None:
            self.db.commit()
        return self._search_registry_topics(query, [knowledge_base_id], limit, principal)

    def _search_registry_topics(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        limit: int,
        principal: Principal,
    ) -> list[LLMWikiTopicCandidate]:
        if self.db is None or not knowledge_base_ids:
            return []
        normalized_query = query.strip()
        rows = self.db.execute(
            text(
                """
                SELECT id, knowledge_base_id, canonical_topic, page_type, status, aliases,
                       quality_score, source_document_count, source_chunk_count,
                       GREATEST(
                           similarity(canonical_topic, :query),
                           similarity(COALESCE(aliases::text, ''), :query)
                       ) AS match_score
                FROM llmwiki_topics
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                  AND status IN ('approved', 'compiled')
                  AND (:query = '' OR canonical_topic ILIKE :like_query OR aliases::text ILIKE :like_query)
                ORDER BY match_score DESC, quality_score DESC, updated_at DESC
                LIMIT :limit
                """
            ),
            {
                "query": normalized_query,
                "like_query": f"%{normalized_query}%",
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "limit": limit,
            },
        ).mappings()
        candidates: list[LLMWikiTopicCandidate] = []
        for row in rows:
            if not self._can_access_knowledge_base(principal, row["knowledge_base_id"]):
                continue
            related = self._related_topics_for_topic(row["id"], row["canonical_topic"], limit=5)
            candidates.append(
                LLMWikiTopicCandidate(
                    topic=row["canonical_topic"],
                    score=float(row["match_score"] or row["quality_score"] or 0.0),
                    evidence_count=int(row["source_chunk_count"] or 0),
                    document_count=int(row["source_document_count"] or 0),
                    related_topics=related,
                    page_type=row["page_type"] or "unknown",
                    status=row["status"],
                    quality_score=float(row["quality_score"] or 0.0),
                )
            )
        return candidates

    def _related_topics_for_topic(self, topic_id: UUID, topic: str, limit: int) -> list[str]:
        if self.db is None:
            return []
        rows = self.db.execute(
            text(
                """
                SELECT other.canonical_topic, COUNT(*) AS overlap
                FROM llmwiki_topic_evidence own
                JOIN llmwiki_topic_evidence oe ON oe.chunk_id = own.chunk_id
                JOIN llmwiki_topics other ON other.id = oe.topic_id
                WHERE own.topic_id = :topic_id
                  AND other.id <> :topic_id
                  AND other.status IN ('approved', 'compiled')
                  AND lower(other.canonical_topic) <> lower(:topic)
                GROUP BY other.canonical_topic, other.quality_score
                ORDER BY overlap DESC, other.quality_score DESC
                LIMIT :limit
                """
            ),
            {"topic_id": str(topic_id), "topic": topic, "limit": limit},
        ).mappings()
        return [row["canonical_topic"] for row in rows if not self._is_noise_topic(row["canonical_topic"])]

    def build_reference_context(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        principal: Principal,
        limit: int = 3,
        max_chars: int = 6000,
    ) -> str:
        if self.db is None or not knowledge_base_ids:
            return ""
        normalized_query = query.strip()
        rows = self.db.execute(
            text(
                """
                SELECT id, knowledge_base_id, topic, slug, summary, content_markdown,
                       key_points, linked_topics, source_document_count,
                       source_chunk_count, updated_at,
                       GREATEST(
                           similarity(topic, :query),
                           similarity(summary, :query),
                           similarity(content_markdown, :query)
                       ) AS score
                FROM llmwiki_pages
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                  AND COALESCE((metadata->>'schema_version')::int, 1) >= :schema_version
                ORDER BY score DESC, updated_at DESC
                LIMIT :limit
                """
            ),
            {
                "query": normalized_query,
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "limit": limit,
                "schema_version": _SCHEMA_VERSION,
            },
        ).mappings()
        sections: list[str] = []
        used_chars = 0
        query_terms = {term.lower() for term in self._extract_terms(normalized_query)}
        for row_index, row in enumerate(rows):
            if not self._can_access_knowledge_base(principal, row["knowledge_base_id"]):
                continue
            page_terms = {
                term.lower()
                for term in self._extract_terms(
                    " ".join(
                        [
                            str(row["topic"] or ""),
                            str(row["summary"] or ""),
                            str(row["content_markdown"] or "")[:1800],
                        ]
                    )
                )
            }
            overlap = len(query_terms.intersection(page_terms))
            score = float(row["score"] or 0.0)
            if row_index > 0 and normalized_query and query_terms and overlap == 0 and score < 0.08:
                continue
            links = [
                link.get("topic", "")
                for link in self._json_list(row["linked_topics"])
                if link.get("topic") and not self._is_noise_topic(link.get("topic", ""))
            ][:8]
            content = self._clean_text(row["content_markdown"] or row["summary"])
            section = "\n".join(
                [
                    f"[LLMWiki:{row['slug']}] {row['topic']}",
                    f"Compiled: {self._iso(row['updated_at'])}",
                    f"Sources: {row['source_document_count']} documents, {row['source_chunk_count']} chunks",
                    f"Summary: {row['summary']}",
                    f"Related: {', '.join(links) if links else 'none'}",
                    "Compiled page:",
                    content[:1800],
                ]
            )
            if used_chars + len(section) > max_chars:
                remaining = max_chars - used_chars
                if remaining <= 400:
                    break
                section = section[:remaining]
            sections.append(section)
            used_chars += len(section)
            if used_chars >= max_chars:
                break
        return "\n\n---\n\n".join(sections)

    def demo_page(self) -> LLMWikiTopicPage:
        topic = "LLMWiki Compounding Knowledge"
        links = [
            LLMWikiLink(topic="Immutable Sources", direction="bidirectional", strength=0.38, evidence_count=3),
            LLMWikiLink(topic="Wiki Lint", direction="bidirectional", strength=0.31, evidence_count=2),
            LLMWikiLink(topic="Contradiction Tracking", direction="bidirectional", strength=0.24, evidence_count=2),
        ]
        graph = LLMWikiGraph(
            nodes=[
                LLMWikiGraphNode(id="topic:llmwiki", label=topic, type="topic", weight=3),
                LLMWikiGraphNode(id="topic:sources", label="Immutable Sources", type="topic", weight=3),
                LLMWikiGraphNode(id="topic:lint", label="Wiki Lint", type="topic", weight=2),
                LLMWikiGraphNode(id="topic:conflicts", label="Contradiction Tracking", type="topic", weight=2),
            ],
            edges=[
                LLMWikiGraphEdge(source="topic:llmwiki", target="topic:sources", relation="related_to", weight=0.38),
                LLMWikiGraphEdge(source="topic:llmwiki", target="topic:lint", relation="related_to", weight=0.31),
                LLMWikiGraphEdge(source="topic:llmwiki", target="topic:conflicts", relation="related_to", weight=0.24),
            ],
        )
        key_points = [
            "Raw documents stay as immutable evidence while compiled pages keep the durable synthesis.",
            "Each compile refreshes summaries, key points, links, and evidence so the wiki compounds over time.",
            "Linting reports stale pages, missing cross-links, orphan topics, and candidate concepts to compile next.",
        ]
        return LLMWikiTopicPage(
            topic=topic,
            summary="A local LLMWiki stores durable topic pages beside the RAG index, so knowledge is compiled once and maintained over time instead of being re-derived for every question.",
            key_points=key_points,
            linked_topics=links,
            evidence=[],
            graph=graph,
            content_markdown=self._content_markdown(
                topic=topic,
                summary="A local LLMWiki stores durable topic pages beside the RAG index.",
                key_points=key_points,
                related=links,
                evidence=[],
                contradictions=[],
                notes=["Demo content is static and does not require external services."],
            ),
            source_document_count=3,
            source_chunk_count=7,
            maintenance_notes=["Demo content is static and does not require external services."],
        )

    def _load_chunks(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        limit: int,
        principal: Principal,
    ) -> list[_WikiChunk]:
        if self.db is None or not knowledge_base_ids:
            return []

        permission_sql, permission_params = VectorStoreService(self.db)._permission_filter(principal)
        normalized_query = query.strip()
        query_score_sql = (
            """
            GREATEST(
                similarity(c.content, :query),
                similarity(COALESCE(c.section_title, ''), :query),
                similarity(COALESCE(d.title, ''), :query),
                similarity(COALESCE(d.original_filename, d.filename), :query)
            )
            """
            if normalized_query
            else "0.0"
        )
        rows = self.db.execute(
            text(
                f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.knowledge_base_id,
                    c.content,
                    c.section_title,
                    c.page_start,
                    c.page_end,
                    c.confidential_level,
                    c.source_type,
                    c.metadata,
                    c.created_at,
                    COALESCE(d.title, d.original_filename, d.filename) AS document_title,
                    {query_score_sql} AS base_score
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.knowledge_base_id = ANY(:knowledge_base_ids)
                  AND d.status = :ready_status
                  {permission_sql}
                ORDER BY base_score DESC, c.created_at DESC
                LIMIT :candidate_limit
                """
            ),
            {
                "query": normalized_query,
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "ready_status": DocumentStatus.READY.value,
                "candidate_limit": max(limit * 8, limit, 80),
                **permission_params,
            },
        ).mappings()
        chunks = [
            _WikiChunk(
                chunk_id=row["id"],
                document_id=row["document_id"],
                knowledge_base_id=row["knowledge_base_id"],
                content=row["content"],
                document_title=row["document_title"],
                section_title=row["section_title"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                confidential_level=row["confidential_level"],
                source_type=row["source_type"],
                metadata=row["metadata"] or {},
                created_at=row["created_at"],
                score=float(row["base_score"] or 0.0),
            )
            for row in rows
        ]
        return self._rank_chunks(normalized_query, chunks, limit)

    def _rank_chunks(self, query: str, chunks: list[_WikiChunk], limit: int) -> list[_WikiChunk]:
        if not chunks:
            return []
        query_terms = {term.lower() for term in self._extract_terms(query)}
        wants_images = self._query_wants_images(query)
        ranked: list[_WikiChunk] = []
        for chunk in chunks:
            text_value = self._clean_text(
                " ".join([chunk.document_title or "", chunk.section_title or "", chunk.content])
            )
            lowered = text_value.lower()
            content_terms = {term.lower() for term in self._extract_terms(text_value[:3000])}
            overlap = len(query_terms.intersection(content_terms))
            exact_hits = lowered.count(query.lower()) if query else 0
            title_hit = 1 if query and query.lower() in (chunk.document_title or "").lower() else 0
            section_hit = 1 if query and query.lower() in (chunk.section_title or "").lower() else 0
            source_bonus = 0.15 if chunk.source_type in {"docx_text", "pdf_text", "xlsx_text", "pptx_text"} else 0
            raw_score = (
                chunk.score * 6
                + exact_hits * 1.8
                + overlap * 1.2
                + title_hit * 2.2
                + section_hit * 1.4
                + min(len(content_terms), 80) / 200
                + source_bonus
            )
            score = raw_score * self._content_quality_multiplier(chunk, content_terms, wants_images)
            ranked.append(
                _WikiChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    knowledge_base_id=chunk.knowledge_base_id,
                    content=chunk.content,
                    document_title=chunk.document_title,
                    section_title=chunk.section_title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    confidential_level=chunk.confidential_level,
                    source_type=chunk.source_type,
                    metadata=chunk.metadata,
                    created_at=chunk.created_at,
                    score=round(score, 4),
                )
            )
        ranked.sort(key=lambda item: item.score, reverse=True)
        selected: list[_WikiChunk] = []
        per_document: Counter[UUID] = Counter()
        max_per_document = max(3, limit // 3)
        image_count = 0
        max_image_chunks = max(2, limit // 4) if wants_images else 0
        for chunk in ranked:
            if per_document[chunk.document_id] >= max_per_document and len(per_document) > 1:
                continue
            if chunk.source_type == "pdf_image":
                if image_count >= max_image_chunks:
                    continue
                image_count += 1
            selected.append(chunk)
            per_document[chunk.document_id] += 1
            if len(selected) >= limit:
                break
        return selected

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

    def _content_quality_multiplier(
        self,
        chunk: _WikiChunk,
        content_terms: set[str],
        wants_images: bool,
    ) -> float:
        lowered = chunk.content.lower()
        multiplier = 1.0
        if chunk.source_type == "pdf_image":
            multiplier *= 0.75 if wants_images else 0.35
        if self._looks_like_reference_noise(lowered):
            multiplier *= 0.25
        if len(content_terms) < 6:
            multiplier *= 0.6
        return multiplier

    def _looks_like_reference_noise(self, lowered: str) -> bool:
        head = lowered[:220]
        reference_signals = (
            lowered.count(" doi")
            + lowered.count("http")
            + lowered.count(" et al")
            + lowered.count("journal")
            + lowered.count("vol.")
        )
        return "references" in head or reference_signals >= 3

    def _topic_signals_from_chunks(self, chunks: list[_WikiChunk], limit: int) -> list[_TopicSignal]:
        buckets: dict[str, dict] = {}
        for chunk in chunks:
            candidates = self._candidate_phrases(chunk)
            for raw_topic, source_weight in candidates:
                topic = self._normalize_topic(raw_topic)
                if self._is_noise_topic(topic):
                    continue
                key = topic.lower()
                bucket = buckets.setdefault(
                    key,
                    {
                        "topic": topic,
                        "score": 0.0,
                        "chunks": {},
                        "aliases": Counter(),
                    },
                )
                bucket["score"] += max(chunk.score, 0.25) + source_weight
                bucket["chunks"][chunk.chunk_id] = chunk
                if raw_topic != topic:
                    bucket["aliases"][raw_topic] += 1

        signals: list[_TopicSignal] = []
        for bucket in buckets.values():
            signal_chunks = list(bucket["chunks"].values())
            document_count = len({chunk.document_id for chunk in signal_chunks})
            if self._is_low_value_topic(bucket["topic"], signal_chunks, document_count):
                continue
            aliases = [alias for alias, _ in bucket["aliases"].most_common(5)]
            signals.append(
                _TopicSignal(
                    topic=bucket["topic"],
                    score=round(float(bucket["score"]), 4),
                    chunks=signal_chunks[:12],
                    aliases=aliases,
                    page_type=self._guess_page_type(bucket["topic"]),
                )
            )
        signals.sort(
            key=lambda item: (
                item.score,
                len({chunk.document_id for chunk in item.chunks}),
                len(item.topic),
            ),
            reverse=True,
        )
        return signals[:limit]

    def _candidate_phrases(self, chunk: _WikiChunk) -> list[tuple[str, float]]:
        values = [
            (chunk.section_title or "", 2.4),
            (chunk.document_title or "", 1.8),
            (str(chunk.metadata.get("title") or ""), 1.8),
            (chunk.content[:2200], 1.0),
        ]
        candidates: list[tuple[str, float]] = []
        for value, weight in values:
            cleaned = self._strip_noise_text(value)
            if not cleaned:
                continue
            candidates.extend((phrase, weight) for phrase in self._latin_phrases(cleaned))
            candidates.extend((phrase, weight) for phrase in _CJK_RUN_PATTERN.findall(cleaned))
        return candidates

    def _latin_phrases(self, value: str) -> list[str]:
        token_pattern = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
        tokens = token_pattern.findall(value)
        phrases: Counter[str] = Counter()
        window: list[str] = []
        for token in tokens:
            lowered = token.lower().strip("-")
            if self._is_noise_token(lowered):
                if len(window) >= 2:
                    self._add_ngram_phrases(window, phrases)
                window = []
                continue
            window.append(token.strip("-"))
            if len(window) > 6:
                window = window[-6:]
        if len(window) >= 2:
            self._add_ngram_phrases(window, phrases)
        return [phrase for phrase, _ in phrases.most_common(24)]

    def _add_ngram_phrases(self, tokens: list[str], phrases: Counter[str]) -> None:
        for size in range(min(5, len(tokens)), 1, -1):
            for index in range(0, len(tokens) - size + 1):
                phrase = " ".join(tokens[index : index + size])
                normalized = self._normalize_topic(phrase)
                if not self._is_noise_topic(normalized):
                    phrases[normalized] += size

    def _strip_noise_text(self, value: str) -> str:
        value = re.sub(r"https?://\S+|www\.\S+", " ", value)
        value = re.sub(r"\bdoi\s*:?\s*\S+|\b10\.\d{4,9}/\S+", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", " ", value)
        value = re.sub(r"\b\w+\.(?:com|org|net|edu|gov|io|ai|cn|tw|uk|de|jp)\b", " ", value, flags=re.IGNORECASE)
        return self._clean_text(value)

    def _normalize_topic(self, value: str) -> str:
        cleaned = re.sub(r"[_/\\|:;,.()[\]{}<>]+", " ", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        if not cleaned:
            return ""
        if re.search(r"[A-Z]", cleaned):
            words = [
                word
                if word.isupper() or any(char.isupper() for char in word[1:])
                else word.capitalize()
                for word in cleaned.split()
            ]
            return " ".join(words)
        return cleaned

    def _is_noise_token(self, lowered: str) -> bool:
        if not lowered or lowered in _NOISE_TERMS:
            return True
        if lowered.isdigit() or len(lowered) <= 2:
            return True
        if re.fullmatch(r"\d+[a-z]?", lowered):
            return True
        return False

    def _is_noise_topic(self, topic: str) -> bool:
        normalized = self._clean_text(topic).strip("-_ ")
        if not normalized:
            return True
        lowered = normalized.lower()
        if lowered in _NOISE_TERMS:
            return True
        if re.search(r"https?://|www\.|doi\.org|@", lowered):
            return True
        words = re.findall(r"[A-Za-z][A-Za-z0-9\-]*", normalized)
        if words and all(self._is_noise_token(word.lower()) for word in words):
            return True
        return False

    def _is_low_value_topic(
        self,
        topic: str,
        chunks: list[_WikiChunk],
        document_count: int,
    ) -> bool:
        if self._is_noise_topic(topic):
            return True
        word_count = len(re.findall(r"[A-Za-z][A-Za-z0-9\-]*", topic))
        has_cjk = bool(_CJK_RUN_PATTERN.search(topic))
        if word_count == 1 and not has_cjk:
            return document_count < 2 and topic.lower() not in _ALLOWED_SINGLE_WORD_TOPICS
        return not chunks

    def _guess_page_type(self, topic: str) -> str:
        lowered = topic.lower()
        if any(marker in lowered for marker in (" vs ", " versus ", "comparison")):
            return "comparison"
        if any(marker in lowered for marker in ("overview", "synthesis", "summary")):
            return "synthesis"
        return "concept"

    def _deterministic_review(self, signal: _TopicSignal) -> _TopicReview:
        rejected = self._is_noise_topic(signal.topic) or self._is_low_value_topic(
            signal.topic,
            signal.chunks,
            len({chunk.document_id for chunk in signal.chunks}),
        )
        return _TopicReview(
            decision="rejected" if rejected else "approved",
            canonical_topic=signal.topic,
            page_type=signal.page_type,
            aliases=signal.aliases,
            quality_score=0.0 if rejected else min(1.0, max(0.45, signal.score / 20)),
            confidence=0.95 if rejected else 0.6,
            rejection_reason="deterministic_noise_filter" if rejected else "",
            related_topic_hints=[],
        )

    async def _llm_review_signal(self, signal: _TopicSignal) -> _TopicReview:
        deterministic = self._deterministic_review(signal)
        if self._is_noise_topic(signal.topic):
            return deterministic
        snippets = [
            {
                "document_title": chunk.document_title,
                "section_title": chunk.section_title,
                "snippet": self._snippet(chunk.content, signal.topic, max_chars=240),
            }
            for chunk in signal.chunks[:5]
        ]
        system_prompt = (
            "You review candidate topics for an enterprise LLM-maintained wiki. "
            "Reject stopwords, URL/domain fragments, DOI/reference noise, generic function words, "
            "and terms that are not useful standalone wiki pages. Return JSON only."
        )
        user_prompt = json.dumps(
            {
                "candidate": signal.topic,
                "aliases": signal.aliases,
                "page_type_guess": signal.page_type,
                "source_document_count": len({chunk.document_id for chunk in signal.chunks}),
                "source_chunk_count": len(signal.chunks),
                "supporting_snippets": snippets,
                "required_json": {
                    "decision": "approved|rejected",
                    "canonical_topic": "string",
                    "page_type": "concept|entity|comparison|synthesis|source|unknown",
                    "aliases": ["string"],
                    "quality_score": 0.0,
                    "confidence": 0.0,
                    "rejection_reason": "string",
                    "related_topic_hints": ["string"],
                },
            },
            ensure_ascii=False,
        )
        try:
            raw = await self.llm_service.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception:
            return deterministic
        try:
            payload = self._extract_json_object(raw)
        except Exception as exc:
            return _TopicReview(
                decision="rejected",
                canonical_topic=signal.topic,
                page_type=signal.page_type,
                aliases=signal.aliases,
                quality_score=0.0,
                confidence=0.0,
                rejection_reason=f"llm_review_failed: {exc}",
                related_topic_hints=[],
            )
        return self._review_from_payload(signal, payload)

    def _extract_json_object(self, value: str) -> dict:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", value, flags=re.DOTALL)
            if not match:
                raise
            decoded = json.loads(match.group(0))
        if not isinstance(decoded, dict):
            raise ValueError("LLM review did not return a JSON object.")
        return decoded

    def _review_from_payload(self, signal: _TopicSignal, payload: dict) -> _TopicReview:
        decision = str(payload.get("decision") or "").lower()
        canonical = self._normalize_topic(str(payload.get("canonical_topic") or signal.topic))
        if decision not in {"approved", "rejected"}:
            decision = "rejected"
        if decision == "approved" and self._is_noise_topic(canonical):
            decision = "rejected"
        aliases = payload.get("aliases") if isinstance(payload.get("aliases"), list) else signal.aliases
        hints = (
            payload.get("related_topic_hints")
            if isinstance(payload.get("related_topic_hints"), list)
            else []
        )
        return _TopicReview(
            decision=decision,
            canonical_topic=canonical or signal.topic,
            page_type=str(payload.get("page_type") or signal.page_type or "unknown"),
            aliases=[str(item) for item in aliases if str(item).strip()][:8],
            quality_score=float(payload.get("quality_score") or (0.0 if decision == "rejected" else 0.5)),
            confidence=float(payload.get("confidence") or 0.0),
            rejection_reason=str(payload.get("rejection_reason") or ""),
            related_topic_hints=[str(item) for item in hints if str(item).strip()][:8],
        )

    def _ensure_topic_review(
        self,
        topic: str,
        chunks: list[_WikiChunk],
        knowledge_base_id: UUID,
        reviewer: str,
    ) -> dict | None:
        if self.db is None:
            return None
        existing = self._load_topic_row(topic, [knowledge_base_id])
        if existing and existing["status"] in {"approved", "compiled", "rejected"}:
            return existing
        signal = self._signal_for_requested_topic(topic, chunks)
        review = self._deterministic_review(signal)
        return self._upsert_topic_review(signal, knowledge_base_id, review, reviewer, commit=True)

    async def _ensure_topic_review_async(
        self,
        topic: str,
        chunks: list[_WikiChunk],
        knowledge_base_id: UUID,
    ) -> dict | None:
        if self.db is None:
            return None
        existing = self._load_topic_row(topic, [knowledge_base_id])
        if existing and existing["status"] in {"approved", "compiled", "rejected"}:
            return existing
        signal = self._signal_for_requested_topic(topic, chunks)
        review = await self._llm_review_signal(signal)
        return self._upsert_topic_review(signal, knowledge_base_id, review, "llm", commit=True)

    def _signal_for_requested_topic(self, topic: str, chunks: list[_WikiChunk]) -> _TopicSignal:
        normalized = self._normalize_topic(topic)
        return _TopicSignal(
            topic=normalized,
            score=sum(max(chunk.score, 0.25) for chunk in chunks),
            chunks=chunks[:12],
            aliases=[],
            page_type=self._guess_page_type(normalized),
        )

    def _load_topic_row(self, topic: str, knowledge_base_ids: list[UUID]) -> dict | None:
        if self.db is None or not knowledge_base_ids:
            return None
        slug = self._slugify(topic)
        row = self.db.execute(
            text(
                """
                SELECT id, knowledge_base_id, canonical_topic, slug, page_type, status,
                       aliases, quality_score, llm_confidence, rejection_reason,
                       source_document_count, source_chunk_count, metadata, updated_at
                FROM llmwiki_topics
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                  AND (slug = :slug OR lower(canonical_topic) = lower(:topic))
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "slug": slug,
                "topic": topic,
            },
        ).mappings().first()
        return dict(row) if row else None

    def _upsert_topic_review(
        self,
        signal: _TopicSignal,
        knowledge_base_id: UUID,
        review: _TopicReview,
        reviewer: str,
        commit: bool,
    ) -> dict | None:
        if self.db is None:
            return None
        canonical = self._normalize_topic(review.canonical_topic or signal.topic)
        slug = self._slugify(canonical)
        now = datetime.utcnow()
        status = "approved" if review.decision == "approved" else "rejected"
        metadata = {
            "reviewer": reviewer,
            "schema_version": _SCHEMA_VERSION,
            "candidate_topic": signal.topic,
            "related_topic_hints": review.related_topic_hints or [],
            "signal_score": signal.score,
        }
        existing = self.db.execute(
            text(
                """
                SELECT id FROM llmwiki_topics
                WHERE knowledge_base_id = :knowledge_base_id AND slug = :slug
                """
            ),
            {"knowledge_base_id": str(knowledge_base_id), "slug": slug},
        ).mappings().first()
        source_document_count = len({chunk.document_id for chunk in signal.chunks})
        source_chunk_count = len(signal.chunks)
        if existing:
            topic_id = existing["id"]
            self.db.execute(
                text(
                    """
                    UPDATE llmwiki_topics
                    SET canonical_topic = :canonical_topic,
                        page_type = :page_type,
                        status = :status,
                        aliases = CAST(:aliases AS jsonb),
                        quality_score = :quality_score,
                        llm_confidence = :llm_confidence,
                        rejection_reason = :rejection_reason,
                        source_document_count = :source_document_count,
                        source_chunk_count = :source_chunk_count,
                        metadata = CAST(:metadata AS jsonb),
                        updated_at = :updated_at
                    WHERE id = :topic_id
                    """
                ),
                {
                    "topic_id": str(topic_id),
                    "canonical_topic": canonical,
                    "page_type": review.page_type,
                    "status": status,
                    "aliases": json.dumps(review.aliases, ensure_ascii=False),
                    "quality_score": review.quality_score,
                    "llm_confidence": review.confidence,
                    "rejection_reason": review.rejection_reason,
                    "source_document_count": source_document_count,
                    "source_chunk_count": source_chunk_count,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "updated_at": now,
                },
            )
        else:
            topic_id = self.db.execute(
                text(
                    """
                    INSERT INTO llmwiki_topics (
                        knowledge_base_id, canonical_topic, slug, page_type, status,
                        aliases, quality_score, llm_confidence, rejection_reason,
                        source_document_count, source_chunk_count, metadata,
                        created_at, updated_at
                    )
                    VALUES (
                        :knowledge_base_id, :canonical_topic, :slug, :page_type, :status,
                        CAST(:aliases AS jsonb), :quality_score, :llm_confidence,
                        :rejection_reason, :source_document_count, :source_chunk_count,
                        CAST(:metadata AS jsonb), :created_at, :updated_at
                    )
                    RETURNING id
                    """
                ),
                {
                    "knowledge_base_id": str(knowledge_base_id),
                    "canonical_topic": canonical,
                    "slug": slug,
                    "page_type": review.page_type,
                    "status": status,
                    "aliases": json.dumps(review.aliases, ensure_ascii=False),
                    "quality_score": review.quality_score,
                    "llm_confidence": review.confidence,
                    "rejection_reason": review.rejection_reason,
                    "source_document_count": source_document_count,
                    "source_chunk_count": source_chunk_count,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                },
            ).scalar_one()

        self.db.execute(
            text("DELETE FROM llmwiki_topic_evidence WHERE topic_id = :topic_id"),
            {"topic_id": str(topic_id)},
        )
        if status != "rejected":
            for rank, chunk in enumerate(signal.chunks[:20], start=1):
                self.db.execute(
                    text(
                        """
                        INSERT INTO llmwiki_topic_evidence (
                            topic_id, chunk_id, document_id, knowledge_base_id,
                            rank, signal_score, metadata
                        )
                        VALUES (
                            :topic_id, :chunk_id, :document_id, :knowledge_base_id,
                            :rank, :signal_score, CAST(:metadata AS jsonb)
                        )
                        ON CONFLICT (topic_id, chunk_id) DO UPDATE
                        SET rank = EXCLUDED.rank,
                            signal_score = EXCLUDED.signal_score,
                            metadata = EXCLUDED.metadata
                        """
                    ),
                    {
                        "topic_id": str(topic_id),
                        "chunk_id": str(chunk.chunk_id),
                        "document_id": str(chunk.document_id),
                        "knowledge_base_id": str(chunk.knowledge_base_id),
                        "rank": rank,
                        "signal_score": chunk.score,
                        "metadata": json.dumps(
                            {
                                "document_title": chunk.document_title,
                                "section_title": chunk.section_title,
                                "source_type": chunk.source_type,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
        self._log_operation(
            "topic_review",
            canonical,
            f"{status.title()} LLMWiki topic '{canonical}'.",
            {
                "topic_id": str(topic_id),
                "knowledge_base_id": str(knowledge_base_id),
                "reviewer": reviewer,
                "status": status,
                "schema_version": _SCHEMA_VERSION,
            },
        )
        if commit:
            self.db.commit()
        return self._load_topic_row(canonical, [knowledge_base_id])

    def _assemble_topic_page(
        self,
        topic: str,
        chunks: list[_WikiChunk],
        include_graph: bool,
        compiled_page_id: UUID | None,
        compiled_slug: str | None,
        last_compiled_at: str | None,
        topic_row: dict | None = None,
    ) -> LLMWikiTopicPage:
        evidence = [self._to_evidence(chunk, topic) for chunk in chunks]
        page_type = (topic_row or {}).get("page_type") or "unknown"
        topic_quality_score = float((topic_row or {}).get("quality_score") or 0.0)
        related = self._linked_topics(topic, chunks)
        key_points = self._key_points(chunks, topic, max_points=6)
        contradictions = self._contradictions(chunks, topic)
        notes = self._maintenance_notes(chunks, related, contradictions)
        if topic_row and topic_row.get("status") == "rejected":
            notes.append(
                f"Topic review rejected this candidate: {topic_row.get('rejection_reason') or 'low topic quality'}."
            )
        summary = self._summary(topic, key_points, evidence)
        graph = self._graph(topic, chunks, related) if include_graph else None
        content_markdown = self._content_markdown(
            topic=topic,
            summary=summary,
            key_points=key_points,
            related=related,
            evidence=evidence,
            contradictions=contradictions,
            notes=notes,
        )
        return LLMWikiTopicPage(
            topic=topic,
            summary=summary,
            page_type=page_type,
            key_points=key_points,
            aliases=self._aliases(topic, chunks),
            linked_topics=related,
            evidence=evidence,
            graph=graph,
            content_markdown=content_markdown,
            compiled_page_id=compiled_page_id,
            compiled_slug=compiled_slug,
            last_compiled_at=last_compiled_at,
            source_document_count=len({chunk.document_id for chunk in chunks}),
            source_chunk_count=len(chunks),
            stale=False,
            topic_quality_score=topic_quality_score,
            contradictions=contradictions,
            maintenance_notes=notes,
        )

    def _to_evidence(self, chunk: _WikiChunk, topic: str) -> LLMWikiEvidence:
        return LLMWikiEvidence(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            knowledge_base_id=chunk.knowledge_base_id,
            document_title=chunk.document_title,
            section_title=chunk.section_title,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            snippet=self._snippet(chunk.content, topic),
            confidential_level=chunk.confidential_level,
            score=chunk.score,
            source_type=chunk.source_type,
        )

    def _linked_topics(self, topic: str, chunks: list[_WikiChunk]) -> list[LLMWikiLink]:
        if self.db is None or not chunks:
            return []
        chunk_ids = {chunk.chunk_id for chunk in chunks}
        kb_ids = {chunk.knowledge_base_id for chunk in chunks}
        rows = self.db.execute(
            text(
                """
                SELECT t.canonical_topic, t.page_type, t.quality_score,
                       COUNT(te.chunk_id) AS evidence_count
                FROM llmwiki_topics t
                JOIN llmwiki_topic_evidence te ON te.topic_id = t.id
                WHERE t.knowledge_base_id = ANY(:knowledge_base_ids)
                  AND t.status IN ('approved', 'compiled')
                  AND lower(t.canonical_topic) <> lower(:topic)
                  AND te.chunk_id = ANY(:chunk_ids)
                GROUP BY t.canonical_topic, t.page_type, t.quality_score
                ORDER BY evidence_count DESC, t.quality_score DESC, t.canonical_topic ASC
                LIMIT 12
                """
            ),
            {
                "knowledge_base_ids": [str(item) for item in kb_ids],
                "chunk_ids": [str(item) for item in chunk_ids],
                "topic": topic,
            },
        ).mappings()
        items = list(rows)
        total = max(sum(int(row["evidence_count"] or 0) for row in items), 1)
        return [
            LLMWikiLink(
                topic=row["canonical_topic"],
                direction="bidirectional",
                strength=round(int(row["evidence_count"] or 0) / total, 4),
                evidence_count=int(row["evidence_count"] or 0),
                page_type=row["page_type"] or "unknown",
                quality_score=float(row["quality_score"] or 0.0),
            )
            for row in items
            if not self._is_noise_topic(row["canonical_topic"])
        ]

    def _graph(
        self,
        topic: str,
        chunks: list[_WikiChunk],
        related: list[LLMWikiLink],
    ) -> LLMWikiGraph:
        nodes: dict[str, LLMWikiGraphNode] = {
            f"topic:{topic.lower()}": LLMWikiGraphNode(
                id=f"topic:{topic.lower()}",
                label=topic,
                type="topic",
                weight=max(len(chunks), 1),
            )
        }
        edges: list[LLMWikiGraphEdge] = []
        topic_node_id = f"topic:{topic.lower()}"

        for link in related:
            related_id = f"topic:{link.topic.lower()}"
            nodes[related_id] = LLMWikiGraphNode(
                id=related_id,
                label=link.topic,
                type="topic",
                weight=max(link.evidence_count, 1),
            )
            edges.append(
                LLMWikiGraphEdge(
                    source=topic_node_id,
                    target=related_id,
                    relation="related_to",
                    weight=link.strength,
                )
            )

        for chunk in chunks:
            doc_id = f"document:{chunk.document_id}"
            chunk_id = f"chunk:{chunk.chunk_id}"
            nodes.setdefault(
                doc_id,
                LLMWikiGraphNode(
                    id=doc_id,
                    label=chunk.document_title or str(chunk.document_id),
                    type="document",
                    weight=1,
                ),
            )
            nodes[doc_id].weight += 1
            nodes[chunk_id] = LLMWikiGraphNode(
                id=chunk_id,
                label=chunk.section_title or self._snippet(chunk.content, topic, max_chars=60),
                type="evidence",
                weight=max(chunk.score, 0.1),
            )
            edges.append(
                LLMWikiGraphEdge(
                    source=topic_node_id,
                    target=doc_id,
                    relation="supported_by",
                    weight=max(chunk.score, 0.1),
                    evidence_chunk_ids=[chunk.chunk_id],
                )
            )
            edges.append(
                LLMWikiGraphEdge(
                    source=doc_id,
                    target=chunk_id,
                    relation="contains",
                    weight=1,
                    evidence_chunk_ids=[chunk.chunk_id],
                )
            )

        return LLMWikiGraph(nodes=list(nodes.values()), edges=edges)

    def _summary(
        self,
        topic: str,
        key_points: list[str],
        evidence: list[LLMWikiEvidence],
    ) -> str:
        if not evidence:
            return f"No accessible indexed knowledge was found for topic '{topic}'."
        source_count = len({item.document_id for item in evidence})
        prefix = (
            f"{topic} is covered by {source_count} accessible source document"
            f"{'' if source_count == 1 else 's'} and {len(evidence)} evidence chunk"
            f"{'' if len(evidence) == 1 else 's'}."
        )
        if key_points:
            return " ".join([prefix, *key_points[:2]])
        return f"{prefix} {evidence[0].snippet}"

    def _key_points(self, chunks: list[_WikiChunk], topic: str, max_points: int) -> list[str]:
        candidates: list[tuple[float, str]] = []
        topic_terms = {term.lower() for term in self._extract_terms(topic)}
        for chunk in chunks:
            for sentence in self._sentences(chunk.content):
                cleaned = self._clean_text(sentence)
                if len(cleaned) < 24:
                    continue
                sentence_terms = {term.lower() for term in self._extract_terms(cleaned)}
                overlap = len(topic_terms.intersection(sentence_terms))
                topic_hit = 1 if topic.lower() in cleaned.lower() else 0
                ideal_length = 1 if 45 <= len(cleaned) <= 220 else 0
                score = chunk.score + topic_hit * 2.0 + overlap * 1.2 + ideal_length
                candidates.append((score, cleaned))
        candidates.sort(key=lambda item: item[0], reverse=True)
        points: list[str] = []
        seen: set[str] = set()
        for _, sentence in candidates:
            fingerprint = self._sentence_fingerprint(sentence)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            points.append(sentence[:320])
            if len(points) >= max_points:
                break
        return points

    def _contradictions(self, chunks: list[_WikiChunk], topic: str) -> list[str]:
        positive_markers = ("increase", "enable", "allow", "support", "improve", "must", "提升", "允許", "支援", "需要")
        negative_markers = ("decrease", "disable", "deny", "not", "cannot", "must not", "降低", "不支援", "不能", "禁止")
        positive: list[str] = []
        negative: list[str] = []
        for chunk in chunks:
            for sentence in self._sentences(chunk.content):
                lowered = sentence.lower()
                if topic.lower() not in lowered and not any(
                    term.lower() in lowered for term in self._extract_terms(topic)
                ):
                    continue
                if any(marker in lowered for marker in positive_markers):
                    positive.append(self._clean_text(sentence))
                if any(marker in lowered for marker in negative_markers):
                    negative.append(self._clean_text(sentence))
        if positive and negative:
            return [
                "Potential disagreement: accessible sources contain both enabling/supporting language and restrictive/negative language for this topic."
            ]
        return []

    def _maintenance_notes(
        self,
        chunks: list[_WikiChunk],
        related: list[LLMWikiLink],
        contradictions: list[str],
    ) -> list[str]:
        notes: list[str] = []
        if not chunks:
            notes.append("No accessible evidence was available; compile again after ingesting sources.")
        if len({chunk.document_id for chunk in chunks}) == 1 and len(chunks) > 1:
            notes.append("Current synthesis depends on one source document; add more sources before treating it as settled.")
        if not related and chunks:
            notes.append("No strong related topic links were found; lint can suggest candidate pages after more ingests.")
        if contradictions:
            notes.append("Review contradiction notes before using this page as policy or operational guidance.")
        return notes

    def _aliases(self, topic: str, chunks: list[_WikiChunk]) -> list[str]:
        aliases: list[str] = []
        topic_lower = topic.lower()
        for chunk in chunks:
            for candidate in [chunk.section_title, chunk.document_title, chunk.metadata.get("title")]:
                if not candidate:
                    continue
                cleaned = self._clean_text(str(candidate))
                if cleaned.lower() != topic_lower and cleaned not in aliases:
                    aliases.append(cleaned)
                if len(aliases) >= 6:
                    return aliases
        return aliases

    def _ranked_terms(self, chunk: _WikiChunk, max_terms: int) -> list[str]:
        weighted_text = " ".join(
            item
            for item in [
                chunk.section_title,
                chunk.section_title,
                chunk.document_title,
                str(chunk.metadata.get("title") or ""),
                chunk.content[:2200],
            ]
            if item
        )
        return self._extract_terms(weighted_text)[:max_terms]

    def _extract_terms(self, value: str) -> list[str]:
        counts: Counter[str] = Counter()
        for raw in _LATIN_WORD_PATTERN.findall(value):
            term = raw.strip("_- ").strip()
            lowered = term.lower()
            if lowered not in _STOPWORDS and not lowered.isdigit():
                counts[term[:80]] += 2
        for raw in _CJK_RUN_PATTERN.findall(value):
            term = raw.strip()
            if term not in _STOPWORDS:
                counts[term[:40]] += 1
        for raw in _TERM_PATTERN.findall(value):
            term = raw.strip("_- ").strip()
            lowered = term.lower()
            if len(lowered) >= 3 and lowered not in _STOPWORDS and not lowered.isdigit():
                counts[term[:80]] += 1
        return [term for term, _ in counts.most_common(30)]

    def _snippet(self, content: str, topic: str, max_chars: int = 320) -> str:
        cleaned = self._clean_text(content)
        topic_index = cleaned.lower().find(topic.lower())
        if topic_index < 0:
            terms = self._extract_terms(topic)
            topic_index = min(
                [index for term in terms if (index := cleaned.lower().find(term.lower())) >= 0],
                default=-1,
            )
        if topic_index < 0:
            return cleaned[:max_chars]
        start = max(topic_index - max_chars // 3, 0)
        end = min(start + max_chars, len(cleaned))
        return cleaned[start:end].strip()

    def _sentences(self, value: str) -> list[str]:
        cleaned = self._clean_text(value)
        matches = _SENTENCE_PATTERN.findall(cleaned)
        if matches:
            return matches
        return [part for part in re.split(r"[\r\n]+", cleaned) if part.strip()]

    def _content_markdown(
        self,
        topic: str,
        summary: str,
        key_points: list[str],
        related: list[LLMWikiLink],
        evidence: list[LLMWikiEvidence],
        contradictions: list[str],
        notes: list[str],
    ) -> str:
        lines = [
            f"# {topic}",
            "",
            "## Summary",
            summary,
            "",
            "## Key Points",
        ]
        lines.extend(f"- {point}" for point in key_points or ["No key points available yet."])
        lines.extend(["", "## Evidence"])
        if evidence:
            for index, item in enumerate(evidence, start=1):
                location = ""
                if item.page_start:
                    location = f", page {item.page_start}"
                lines.append(
                    f"- [{index}] {item.document_title or item.document_id}{location}: {item.snippet}"
                )
        else:
            lines.append("- No accessible evidence was found.")
        lines.extend(["", "## See Also"])
        lines.extend(
            f"- {link.topic} ({link.evidence_count} evidence signals, strength {link.strength})"
            for link in related
        )
        if not related:
            lines.append("- No related topics compiled yet.")
        if contradictions:
            lines.extend(["", "## Contradictions"])
            lines.extend(f"- {item}" for item in contradictions)
        if notes:
            lines.extend(["", "## Maintenance Notes"])
            lines.extend(f"- {item}" for item in notes)
        return "\n".join(lines)

    def _search_compiled_topics(
        self,
        query: str,
        knowledge_base_ids: list[UUID],
        limit: int,
        principal: Principal,
    ) -> list[LLMWikiTopicCandidate]:
        if self.db is None or not knowledge_base_ids:
            return []
        rows = self.db.execute(
            text(
                """
                SELECT id, knowledge_base_id, topic, summary, linked_topics,
                       source_document_count, source_chunk_count,
                       GREATEST(similarity(topic, :query), similarity(summary, :query)) AS score
                FROM llmwiki_pages
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                  AND COALESCE((metadata->>'schema_version')::int, 1) >= :schema_version
                  AND (:query = '' OR topic ILIKE :like_query OR summary ILIKE :like_query)
                ORDER BY score DESC, updated_at DESC
                LIMIT :limit
                """
            ),
            {
                "query": query.strip(),
                "like_query": f"%{query.strip()}%",
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "limit": limit,
                "schema_version": _SCHEMA_VERSION,
            },
        ).mappings()
        candidates: list[LLMWikiTopicCandidate] = []
        for row in rows:
            if not self._can_access_knowledge_base(principal, row["knowledge_base_id"]):
                continue
            candidates.append(
                LLMWikiTopicCandidate(
                    topic=row["topic"],
                    score=float(row["score"] or 0.0),
                    evidence_count=int(row["source_chunk_count"] or 0),
                    document_count=int(row["source_document_count"] or 0),
                    related_topics=[
                        link.get("topic", "")
                        for link in self._json_list(row["linked_topics"])
                        if link.get("topic") and not self._is_noise_topic(link.get("topic", ""))
                    ][:5],
                    page_type="unknown",
                    status="compiled",
                    quality_score=0.0,
                )
            )
        return candidates

    def _load_compiled_page(
        self,
        topic: str,
        knowledge_base_ids: list[UUID],
        include_graph: bool,
        principal: Principal,
    ) -> LLMWikiTopicPage | None:
        if self.db is None or not knowledge_base_ids:
            return None
        slug = self._slugify(topic)
        row = self.db.execute(
            text(
                """
                SELECT id, knowledge_base_id, topic, slug, summary, content_markdown, key_points,
                       linked_topics, source_document_count, source_chunk_count,
                       fingerprint, metadata, updated_at
                FROM llmwiki_pages
                WHERE knowledge_base_id = ANY(:knowledge_base_ids)
                  AND COALESCE((metadata->>'schema_version')::int, 1) >= :schema_version
                  AND (slug = :slug OR lower(topic) = lower(:topic))
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {
                "knowledge_base_ids": [str(item) for item in knowledge_base_ids],
                "slug": slug,
                "topic": topic,
                "schema_version": _SCHEMA_VERSION,
            },
        ).mappings().first()
        if row is None or not self._can_access_knowledge_base(principal, row["knowledge_base_id"]):
            return None

        evidence_rows = self.db.execute(
            text(
                """
                SELECT chunk_id, document_id, knowledge_base_id, score, snippet, metadata
                FROM llmwiki_page_evidence
                WHERE page_id = :page_id
                ORDER BY rank ASC
                """
            ),
            {"page_id": str(row["id"])},
        ).mappings()
        evidence: list[LLMWikiEvidence] = []
        for item in evidence_rows:
            metadata = item["metadata"] or {}
            evidence.append(
                LLMWikiEvidence(
                    chunk_id=item["chunk_id"],
                    document_id=item["document_id"],
                    knowledge_base_id=item["knowledge_base_id"],
                    document_title=metadata.get("document_title"),
                    section_title=metadata.get("section_title"),
                    page_start=metadata.get("page_start"),
                    page_end=metadata.get("page_end"),
                    snippet=item["snippet"],
                    confidential_level=metadata.get("confidential_level", "internal"),
                    score=float(item["score"] or 0.0),
                    source_type=metadata.get("source_type"),
                )
            )
        links = [LLMWikiLink(**item) for item in self._json_list(row["linked_topics"])]
        metadata = row["metadata"] or {}
        current = self._fingerprint(
            self._load_chunks(
                query=row["topic"],
                knowledge_base_ids=[row["knowledge_base_id"]],
                limit=50,
                principal=principal,
            )
        )
        graph = self._graph(
            row["topic"],
            [
                _WikiChunk(
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    knowledge_base_id=item.knowledge_base_id,
                    content=item.snippet,
                    document_title=item.document_title,
                    section_title=item.section_title,
                    page_start=item.page_start,
                    page_end=item.page_end,
                    confidential_level=item.confidential_level,
                    source_type=item.source_type,
                    metadata={},
                    created_at=None,
                    score=item.score,
                )
                for item in evidence
            ],
            links,
        ) if include_graph else None
        return LLMWikiTopicPage(
            topic=row["topic"],
            summary=row["summary"],
            page_type=metadata.get("page_type", "unknown"),
            key_points=self._json_list(row["key_points"]),
            aliases=metadata.get("aliases", []),
            linked_topics=links,
            evidence=evidence,
            graph=graph,
            content_markdown=row["content_markdown"],
            compiled_page_id=row["id"],
            compiled_slug=row["slug"],
            last_compiled_at=self._iso(row["updated_at"]),
            source_document_count=int(row["source_document_count"] or 0),
            source_chunk_count=int(row["source_chunk_count"] or 0),
            stale=current != row["fingerprint"],
            topic_quality_score=float(metadata.get("topic_quality_score") or 0.0),
            contradictions=metadata.get("contradictions", []),
            maintenance_notes=metadata.get("maintenance_notes", []),
        )

    def _fingerprint(self, chunks: list[_WikiChunk]) -> str:
        payload = "|".join(
            f"{chunk.chunk_id}:{chunk.score}:{hashlib.sha1(chunk.content[:800].encode('utf-8')).hexdigest()}"
            for chunk in chunks
        )
        return hashlib.sha256(f"schema:{_SCHEMA_VERSION}|{payload}".encode("utf-8")).hexdigest()

    def _can_access_knowledge_base(self, principal: Principal, knowledge_base_id: UUID) -> bool:
        if self.db is None:
            return True
        knowledge_base = self.db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None:
            return False
        return PermissionService(self.db).can_access_knowledge_base(principal, knowledge_base)

    def _log_operation(
        self,
        operation: str,
        topic: str | None,
        message: str,
        metadata: dict,
        commit: bool = False,
    ) -> None:
        if self.db is None:
            return
        self.db.execute(
            text(
                """
                INSERT INTO llmwiki_operation_logs (operation, topic, message, metadata, created_at)
                VALUES (:operation, :topic, :message, CAST(:metadata AS jsonb), :created_at)
                """
            ),
            {
                "operation": operation,
                "topic": topic,
                "message": message,
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "created_at": datetime.utcnow(),
            },
        )
        if commit:
            self.db.commit()

    def _json_list(self, value) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return []
            return decoded if isinstance(decoded, list) else []
        return []

    def _sentence_fingerprint(self, value: str) -> str:
        terms = self._extract_terms(value)
        if terms:
            return "|".join(term.lower() for term in terms[:8])
        return value[:80].lower()

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.strip().lower()).strip("-")
        return slug[:80] or "untitled"

    def _iso(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def _ensure_schema(self) -> None:
        self.db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS llmwiki_pages (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
                    topic TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    content_markdown TEXT NOT NULL,
                    key_points JSONB DEFAULT '[]'::jsonb,
                    linked_topics JSONB DEFAULT '[]'::jsonb,
                    source_document_count INT DEFAULT 0,
                    source_chunk_count INT DEFAULT 0,
                    fingerprint TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (knowledge_base_id, slug)
                );

                CREATE TABLE IF NOT EXISTS llmwiki_topics (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
                    canonical_topic TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    page_type TEXT NOT NULL DEFAULT 'unknown',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    aliases JSONB DEFAULT '[]'::jsonb,
                    quality_score FLOAT DEFAULT 0,
                    llm_confidence FLOAT DEFAULT 0,
                    rejection_reason TEXT,
                    source_document_count INT DEFAULT 0,
                    source_chunk_count INT DEFAULT 0,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (knowledge_base_id, slug)
                );

                CREATE TABLE IF NOT EXISTS llmwiki_topic_evidence (
                    topic_id UUID NOT NULL REFERENCES llmwiki_topics(id) ON DELETE CASCADE,
                    chunk_id UUID NOT NULL REFERENCES document_chunks(id),
                    document_id UUID NOT NULL REFERENCES documents(id),
                    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
                    rank INT NOT NULL,
                    signal_score FLOAT DEFAULT 0,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    PRIMARY KEY (topic_id, chunk_id)
                );

                CREATE TABLE IF NOT EXISTS llmwiki_page_evidence (
                    page_id UUID NOT NULL REFERENCES llmwiki_pages(id) ON DELETE CASCADE,
                    chunk_id UUID NOT NULL REFERENCES document_chunks(id),
                    document_id UUID NOT NULL REFERENCES documents(id),
                    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
                    rank INT NOT NULL,
                    score FLOAT DEFAULT 0,
                    snippet TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    PRIMARY KEY (page_id, chunk_id)
                );

                CREATE TABLE IF NOT EXISTS llmwiki_operation_logs (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    operation TEXT NOT NULL,
                    topic TEXT,
                    message TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_llmwiki_pages_kb ON llmwiki_pages(knowledge_base_id);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_pages_slug ON llmwiki_pages(knowledge_base_id, slug);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_topics_kb ON llmwiki_topics(knowledge_base_id);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_topics_slug ON llmwiki_topics(knowledge_base_id, slug);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_topics_status ON llmwiki_topics(knowledge_base_id, status);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_topic_evidence_chunk ON llmwiki_topic_evidence(chunk_id);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_evidence_chunk ON llmwiki_page_evidence(chunk_id);
                CREATE INDEX IF NOT EXISTS idx_llmwiki_logs_created_at ON llmwiki_operation_logs(created_at);
                """
            )
        )
        self.db.commit()
