from fastapi import APIRouter

from app.api.v1 import (
    admin,
    analysis,
    audit,
    chat,
    code_chat,
    documents,
    health,
    knowledge_bases,
    llmwiki,
    permissions,
    skills,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(code_chat.router, prefix="/code-chat", tags=["code-chat"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(skills.router, prefix="/skills", tags=["skills"])
api_router.include_router(
    knowledge_bases.router,
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
)
api_router.include_router(llmwiki.router, prefix="/llmwiki", tags=["llmwiki"])
api_router.include_router(permissions.router, prefix="/permissions", tags=["permissions"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
