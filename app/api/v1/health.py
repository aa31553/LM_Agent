from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

router = APIRouter()


@router.get("")
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "ai-agent-api",
        "version": settings.app_version,
    }


@router.get("/dependencies")
async def dependency_check(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("select 1"))
        postgres = "ok"
    except Exception:
        postgres = "error"
    return {
        "status": "ok" if postgres == "ok" else "degraded",
        "postgres": postgres,
        "redis": "unknown",
        "embedding_service": "unknown",
        "llm_service": "unknown",
    }
