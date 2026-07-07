from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import Principal, get_current_principal
from app.db.session import get_db
from app.schemas.llmwiki import (
    LLMWikiCompileResponse,
    LLMWikiGraph,
    LLMWikiIndexResponse,
    LLMWikiLintResponse,
    LLMWikiOperationLogResponse,
    LLMWikiSearchResponse,
    LLMWikiTopicPage,
)
from app.services.llmwiki_service import LLMWikiService

router = APIRouter()

KnowledgeBaseIds = Annotated[
    list[UUID],
    Query(
        min_length=1,
        description="Knowledge bases used as LLMWiki sources. Results are permission-filtered.",
    ),
]


@router.get("/search", response_model=LLMWikiSearchResponse)
async def search_topics(
    knowledge_base_ids: KnowledgeBaseIds,
    q: str = Query(default="", description="Topic or natural-language search text."),
    limit: int = Query(default=10, ge=1, le=50),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiSearchResponse:
    items = await LLMWikiService(db).search_topics_reviewed(
        query=q,
        knowledge_base_ids=knowledge_base_ids,
        limit=limit,
        principal=principal,
    )
    return LLMWikiSearchResponse(items=items)


@router.get("/index", response_model=LLMWikiIndexResponse)
async def list_compiled_pages(
    knowledge_base_ids: KnowledgeBaseIds,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiIndexResponse:
    return LLMWikiIndexResponse(
        items=LLMWikiService(db).list_index(
            knowledge_base_ids=knowledge_base_ids,
            principal=principal,
        )
    )


@router.get("/demo", response_model=LLMWikiTopicPage)
async def get_demo_page() -> LLMWikiTopicPage:
    return LLMWikiService().demo_page()


@router.post("/topics/{topic}/compile", response_model=LLMWikiCompileResponse)
async def compile_topic_page(
    topic: str,
    knowledge_base_ids: KnowledgeBaseIds,
    top_k: int = Query(default=24, ge=1, le=80),
    include_graph: bool = Query(default=True),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiCompileResponse:
    page, operation = await LLMWikiService(db).compile_topic_reviewed(
        topic=topic,
        knowledge_base_ids=knowledge_base_ids,
        top_k=top_k,
        include_graph=include_graph,
        principal=principal,
    )
    return LLMWikiCompileResponse(page=page, operation=operation)


@router.get("/topics/{topic}", response_model=LLMWikiTopicPage)
async def get_topic_page(
    topic: str,
    knowledge_base_ids: KnowledgeBaseIds,
    top_k: int = Query(default=12, ge=1, le=50),
    include_graph: bool = Query(default=True),
    prefer_compiled: bool = Query(default=True),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiTopicPage:
    return LLMWikiService(db).build_topic_page(
        topic=topic,
        knowledge_base_ids=knowledge_base_ids,
        top_k=top_k,
        include_graph=include_graph,
        principal=principal,
        prefer_compiled=prefer_compiled,
    )


@router.get("/topics/{topic}/graph", response_model=LLMWikiGraph)
async def get_topic_graph(
    topic: str,
    knowledge_base_ids: KnowledgeBaseIds,
    top_k: int = Query(default=20, ge=1, le=80),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiGraph:
    return LLMWikiService(db).build_topic_graph(
        topic=topic,
        knowledge_base_ids=knowledge_base_ids,
        top_k=top_k,
        principal=principal,
    )


@router.get("/lint", response_model=LLMWikiLintResponse)
async def lint_wiki(
    knowledge_base_ids: KnowledgeBaseIds,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiLintResponse:
    return LLMWikiService(db).lint(
        knowledge_base_ids=knowledge_base_ids,
        principal=principal,
    )


@router.get("/operations", response_model=LLMWikiOperationLogResponse)
async def list_operations(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> LLMWikiOperationLogResponse:
    return LLMWikiOperationLogResponse(
        items=LLMWikiService(db).list_operations(limit=limit, principal=principal)
    )
