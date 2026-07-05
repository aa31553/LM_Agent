import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.services.processing_queue_service import ProcessingQueueService

router = APIRouter()


@router.get("")
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "ai-agent-api",
        "version": settings.app_version,
    }


@router.get("/dependencies")
async def dependency_check(db: Session = Depends(get_db)) -> dict[str, Any]:
    postgres = _check_postgres(db)
    storage = _check_storage()
    redis = _check_redis()
    queue = _check_queue(db)
    embedding_service = _check_configured_service(
        endpoint=settings.embedding_endpoint,
        model=settings.embedding_model,
    )
    llm_service = _check_configured_service(
        endpoint=settings.llm_base_url,
        model=settings.llm_model,
    )
    dependencies = {
        "postgres": postgres,
        "redis": redis,
        "storage": storage,
        "queue": queue,
        "embedding_service": embedding_service,
        "llm_service": llm_service,
    }
    status = "ok" if all(item["status"] == "ok" for item in dependencies.values()) else "degraded"
    return {
        "status": status,
        "dependencies": dependencies,
    }


def _check_postgres(db: Session) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        db.execute(text("select 1")).scalar_one()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    return {
        "status": "ok",
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
    }


def _check_storage() -> dict[str, Any]:
    path = Path(settings.local_storage_root)
    writable = path.exists() and path.is_dir() and os.access(path, os.W_OK)
    return {
        "status": "ok" if writable else "error",
        "path": str(path),
        "writable": writable,
    }


def _check_redis() -> dict[str, Any]:
    try:
        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.health_dependency_timeout_seconds,
            socket_timeout=settings.health_dependency_timeout_seconds,
        )
        try:
            client.ping()
        finally:
            client.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "ok"}


def _check_queue(db: Session) -> dict[str, Any]:
    try:
        counts = ProcessingQueueService(db).count_by_status()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "ok", "jobs": counts}


def _check_configured_service(endpoint: str, model: str) -> dict[str, Any]:
    if not endpoint or not model:
        return {"status": "error", "configured": False}
    return {"status": "ok", "configured": True, "endpoint": endpoint, "model": model}
