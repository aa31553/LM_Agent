from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from services.embedding_service.config import EmbeddingServiceSettings


class EmbeddingBackend(Protocol):
    model_name: str
    device: str
    dimension: int

    def load(self) -> None: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...

    def count_tokens(self, texts: list[str]) -> int: ...


class SentenceTransformerBackend:
    def __init__(self, settings: EmbeddingServiceSettings) -> None:
        self.settings = settings
        self.model_name = settings.model_name
        self.device = settings.device
        self.dimension = 0
        self._model = None

    def load(self) -> None:
        if not self.settings.model_path.is_dir():
            raise FileNotFoundError(
                f"Embedding model directory does not exist: {self.settings.model_path}"
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. Install "
                "services/embedding_service/requirements.txt."
            ) from exc

        device = None if self.settings.device == "auto" else self.settings.device
        self._model = SentenceTransformer(
            str(self.settings.model_path.resolve()),
            device=device,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.device = str(self._model.device)
        dimension = self._model.get_sentence_embedding_dimension()
        if not dimension:
            raise RuntimeError("The loaded model did not report an embedding dimension.")
        self.dimension = int(dimension)

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("Embedding model is not loaded.")
        vectors = self._model.encode(
            texts,
            batch_size=min(len(texts), self.settings.max_batch_size),
            convert_to_numpy=True,
            normalize_embeddings=self.settings.normalize_embeddings,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    def count_tokens(self, texts: list[str]) -> int:
        if self._model is None or not hasattr(self._model, "tokenizer"):
            return sum(max(1, len(text.split())) for text in texts)
        encoded = self._model.tokenizer(texts, add_special_tokens=True, truncation=True)
        return sum(len(ids) for ids in encoded.get("input_ids", []))


@dataclass
class RuntimeMetrics:
    started_at: float
    requests_total: int = 0
    requests_failed: int = 0
    inputs_total: int = 0
    last_latency_ms: float | None = None
    total_latency_ms: float = 0.0

    @property
    def average_latency_ms(self) -> float | None:
        successful = self.requests_total - self.requests_failed
        if successful <= 0:
            return None
        return round(self.total_latency_ms / successful, 2)


class EmbeddingRuntime:
    def __init__(
        self,
        settings: EmbeddingServiceSettings,
        backend: EmbeddingBackend | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend or SentenceTransformerBackend(settings)
        self.metrics = RuntimeMetrics(started_at=time.time())
        self.state = "not_loaded"
        self.load_error: str | None = None
        self._load_lock = asyncio.Lock()
        self._encode_lock = asyncio.Lock()
        self._metrics_lock = threading.Lock()

    async def ensure_loaded(self) -> None:
        if self.state == "ready":
            return
        async with self._load_lock:
            if self.state == "ready":
                return
            self.state = "loading"
            self.load_error = None
            try:
                await asyncio.to_thread(self.backend.load)
            except Exception as exc:
                self.state = "error"
                self.load_error = str(exc)
                raise
            self.state = "ready"

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], int, float]:
        with self._metrics_lock:
            self.metrics.requests_total += 1
            self.metrics.inputs_total += len(texts)
        started = time.perf_counter()
        try:
            await self.ensure_loaded()
            async with self._encode_lock:
                vectors, token_count = await asyncio.to_thread(self._encode_sync, texts)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            with self._metrics_lock:
                self.metrics.last_latency_ms = latency_ms
                self.metrics.total_latency_ms += latency_ms
            return vectors, token_count, latency_ms
        except Exception:
            with self._metrics_lock:
                self.metrics.requests_failed += 1
            raise

    def _encode_sync(self, texts: list[str]) -> tuple[list[list[float]], int]:
        vectors = self.backend.encode(texts)
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding backend returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        for vector in vectors:
            if len(vector) != self.backend.dimension:
                raise RuntimeError("Embedding backend returned an inconsistent vector dimension.")
            if any(not math.isfinite(value) for value in vector):
                raise RuntimeError("Embedding backend returned a non-finite value.")
        return vectors, self.backend.count_tokens(texts)

    def status(self) -> dict[str, object]:
        with self._metrics_lock:
            return {
                "status": "ok" if self.state == "ready" else "not_ready",
                "model_state": self.state,
                "model": self.backend.model_name,
                "model_path": str(self.settings.model_path),
                "device": self.backend.device,
                "dimension": self.backend.dimension or None,
                "normalize_embeddings": self.settings.normalize_embeddings,
                "host": self.settings.host,
                "port": self.settings.port,
                "uptime_seconds": round(time.time() - self.metrics.started_at, 2),
                "requests_total": self.metrics.requests_total,
                "requests_failed": self.metrics.requests_failed,
                "inputs_total": self.metrics.inputs_total,
                "last_latency_ms": self.metrics.last_latency_ms,
                "average_latency_ms": self.metrics.average_latency_ms,
                "error": self.load_error,
            }
