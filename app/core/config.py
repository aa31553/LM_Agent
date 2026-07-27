from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LM Agent API"
    app_version: str = "0.7.0"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"
    cors_allow_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    database_url: str = Field(
        default="postgresql+psycopg://lm_agent:lm_agent@localhost:5432/lm_agent"
    )
    redis_url: str = "redis://localhost:6379/0"
    local_storage_root: str = "data/uploads"
    skills_root: str = "data/skills"
    skill_max_file_bytes: int = Field(default=10_000_000, ge=1)
    tesseract_cmd: str = ""

    embedding_endpoint: str = "http://127.0.0.1:1234/v1/embeddings"
    embedding_service_base_url: str = "http://127.0.0.1:1234"
    embedding_model: str = "text-embedding-mxbai-embed-large-v1"
    embedding_dimension: int = 1024
    embedding_batch_size: int = Field(default=16, ge=1)
    embedding_api_key: str = ""
    embedding_timeout_seconds: int = Field(default=90, ge=1)
    embedding_ssl_verify: bool = True

    llm_base_url: str = "http://127.0.0.1:1234"
    llm_api_path: str = "/v1/chat/completions"
    llm_api_key: str = ""
    llm_ssl_verify: bool = True
    llm_model: str = "google/gemma-4-12b-qat"
    llm_timeout_seconds: int = Field(default=90, ge=1)
    llm_temperature: float = 0.1
    llm_top_p: float = 0.9
    llm_max_tokens: int = 1536
    llm_reasoning_effort: str = ""
    llm_send_images_to_model: bool = False

    retrieval_top_k: int = 20
    rerank_top_n: int = 8
    context_max_tokens: int = 8192
    context_max_chars: int = 30000
    vector_score_weight: float = 0.65
    keyword_score_weight: float = 0.35

    document_archive_after_days: int = 365
    retrieval_log_retention_days: int = 90
    masking_event_retention_days: int = 180
    llm_log_retention_days: int = 180
    audit_event_retention_days: int = 365

    logging_level: str = "INFO"
    logging_json: bool = False

    db_connect_timeout_seconds: int = Field(default=5, ge=1)
    health_dependency_timeout_seconds: float = Field(default=2.0, gt=0)

    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_job_timeout_seconds: int = Field(default=900, ge=1)
    worker_retry_attempts: int = Field(default=2, ge=1)
    document_max_upload_bytes: int = Field(default=100_000_000, ge=1)
    pdf_max_file_bytes: int = Field(default=50_000_000, ge=1)
    pdf_max_pages: int = Field(default=200, ge=1)
    pdf_page_timeout_seconds: float = Field(default=20.0, gt=0)
    pdf_job_timeout_seconds: int = Field(default=600, ge=1)
    pdf_ocr_enabled: bool = True
    pdf_image_extraction_enabled: bool = False
    pdf_image_max_pages: int = Field(default=20, ge=0)
    pdf_image_max_count: int = Field(default=50, ge=0)
    pdf_image_ocr_enabled: bool = False
    pdf_image_ocr_max_count: int = Field(default=10, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
