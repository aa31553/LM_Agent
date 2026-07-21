from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


@dataclass(frozen=True)
class EmbeddingServiceSettings:
    model_path: Path
    model_name: str
    device: str
    normalize_embeddings: bool
    max_batch_size: int
    max_input_chars: int
    api_key: str
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "EmbeddingServiceSettings":
        model_path = Path(
            os.getenv(
                "EMBEDDING_MODEL_PATH",
                "models/text-embedding-mxbai-embed-large-v1",
            )
        ).expanduser()
        return cls(
            model_path=model_path,
            model_name=os.getenv(
                "EMBEDDING_MODEL_NAME",
                os.getenv("EMBEDDING_MODEL", model_path.name),
            ),
            device=os.getenv("EMBEDDING_DEVICE", "auto").strip().lower(),
            normalize_embeddings=_env_bool("EMBEDDING_NORMALIZE", True),
            max_batch_size=_env_int("EMBEDDING_MAX_BATCH_SIZE", 32),
            max_input_chars=_env_int("EMBEDDING_MAX_INPUT_CHARS", 100_000),
            api_key=os.getenv("EMBEDDING_SERVICE_API_KEY", "").strip(),
            host=os.getenv("EMBEDDING_HOST", "127.0.0.1").strip(),
            port=_env_int("EMBEDDING_PORT", 1234),
        )
