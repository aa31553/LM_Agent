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
from app.integrations.openai_compatible_client import build_api_url
from app.services.llm_routing_service import LLMRoutingService
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
    llm_service = _check_llm_service()
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


def _check_llm_service() -> dict[str, Any]:
    try:
        default_route = LLMRoutingService().resolve()
    except Exception as exc:
        return {"status": "error", "configured": False, "error": str(exc)}

    configured_routes = {
        selection: {
            "endpoint": build_api_url(route.base_url, route.api_path),
            "model": route.model,
            "allowed_thinking_modes": route.allowed_thinking_modes,
        }
        for selection, route in settings.llm_model_routes.items()
    }
    if default_route.selection not in configured_routes:
        configured_routes[default_route.selection] = {
            "endpoint": build_api_url(default_route.base_url, default_route.api_path),
            "model": default_route.model,
            "allowed_thinking_modes": settings.llm_allowed_thinking_modes,
        }
    return {
        "status": "ok",
        "configured": True,
        "selected_model": default_route.selection,
        "endpoint": build_api_url(default_route.base_url, default_route.api_path),
        "model": default_route.model,
        "available_models": sorted(configured_routes),
        "routes": configured_routes,
    }
