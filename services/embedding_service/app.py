from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from services.embedding_service.config import EmbeddingServiceSettings
from services.embedding_service.runtime import EmbeddingRuntime


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    encoding_format: Literal["float"] = "float"
    dimensions: int | None = Field(default=None, ge=1)


class EmbeddingData(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class Usage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingData]
    model: str
    usage: Usage


def create_app(
    settings: EmbeddingServiceSettings | None = None,
    runtime: EmbeddingRuntime | None = None,
) -> FastAPI:
    service_settings = settings or EmbeddingServiceSettings.from_env()
    service_runtime = runtime or EmbeddingRuntime(service_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            await service_runtime.ensure_loaded()
        except Exception:
            # Keep the HTTP process alive so /status reports the actionable load error.
            pass
        yield

    app = FastAPI(
        title="LM Agent Embedding Service",
        version="1.0.0",
        description="Offline OpenAI-compatible embedding service for LM Agent.",
        lifespan=lifespan,
    )
    app.state.embedding_runtime = service_runtime

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = service_settings.api_key
        if not expected:
            return
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def health_ready(response: Response) -> dict[str, object]:
        payload = service_runtime.status()
        if payload["status"] != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return payload

    @app.get("/status", dependencies=[Depends(authorize)], tags=["monitoring"])
    async def service_status() -> dict[str, object]:
        return service_runtime.status()

    @app.get("/metrics", dependencies=[Depends(authorize)], tags=["monitoring"])
    async def metrics() -> Response:
        payload = service_runtime.status()
        ready = 1 if payload["status"] == "ok" else 0
        lines = [
            "# HELP embedding_service_ready Whether the model is ready.",
            "# TYPE embedding_service_ready gauge",
            f"embedding_service_ready {ready}",
            "# TYPE embedding_requests_total counter",
            f"embedding_requests_total {payload['requests_total']}",
            "# TYPE embedding_requests_failed_total counter",
            f"embedding_requests_failed_total {payload['requests_failed']}",
            "# TYPE embedding_inputs_total counter",
            f"embedding_inputs_total {payload['inputs_total']}",
            "# TYPE embedding_uptime_seconds gauge",
            f"embedding_uptime_seconds {payload['uptime_seconds']}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/v1/models", dependencies=[Depends(authorize)], tags=["openai-compatible"])
    async def list_models() -> dict[str, object]:
        return {
            "object": "list",
            "data": [
                {
                    "id": service_runtime.backend.model_name,
                    "object": "model",
                    "created": int(service_runtime.metrics.started_at),
                    "owned_by": "lm-agent",
                }
            ],
        }

    @app.post(
        "/v1/embeddings",
        response_model=EmbeddingResponse,
        dependencies=[Depends(authorize)],
        tags=["openai-compatible"],
    )
    async def create_embeddings(payload: EmbeddingRequest) -> EmbeddingResponse:
        texts = [payload.input] if isinstance(payload.input, str) else payload.input
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise HTTPException(status_code=400, detail="input must contain non-empty strings.")
        if len(texts) > service_settings.max_batch_size:
            raise HTTPException(
                status_code=400,
                detail=f"input exceeds max batch size {service_settings.max_batch_size}.",
            )
        if sum(len(text) for text in texts) > service_settings.max_input_chars:
            raise HTTPException(
                status_code=413,
                detail="input exceeds the configured character limit.",
            )
        if payload.model and payload.model != service_runtime.backend.model_name:
            raise HTTPException(status_code=404, detail=f"Unknown model: {payload.model}")

        try:
            vectors, token_count, _latency_ms = await service_runtime.embed(texts)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        actual_dimension = len(vectors[0])
        if payload.dimensions is not None and payload.dimensions != actual_dimension:
            raise HTTPException(
                status_code=400,
                detail=f"dimensions must match the loaded model dimension ({actual_dimension}).",
            )
        return EmbeddingResponse(
            data=[
                EmbeddingData(index=index, embedding=vector)
                for index, vector in enumerate(vectors)
            ],
            model=service_runtime.backend.model_name,
            usage=Usage(prompt_tokens=token_count, total_tokens=token_count),
        )

    return app


app = create_app()
