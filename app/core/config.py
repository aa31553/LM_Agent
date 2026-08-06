from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMModelRouteSettings(BaseModel):
    """Server-side allowlisted route for a frontend model selection."""

    base_url: str
    api_path: str = "/v1/chat/completions"
    api_key: str = ""
    ssl_verify: bool = True
    model: str
    timeout_seconds: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    reasoning_effort: str = ""
    allowed_thinking_modes: list[str] = Field(
        default_factory=lambda: ["default", "none", "low", "medium", "high"]
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LM Agent API"
    app_version: str = "0.13.0"
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
    llm_context_window_tokens: int = Field(default=32768, ge=2048)
    llm_prompt_safety_margin_tokens: int = Field(default=1024, ge=0)
    llm_reasoning_effort: str = ""
    llm_default_route: str = ""
    llm_model_routes: dict[str, LLMModelRouteSettings] = Field(default_factory=dict)
    llm_allowed_thinking_modes: list[str] = ["default", "none", "low", "medium", "high"]
    llm_send_images_to_model: bool = False
    chat_request_timeout_seconds: float = Field(default=180.0, gt=0)
    chat_queue_timeout_seconds: float = Field(default=15.0, gt=0)
    chat_max_concurrent_requests: int = Field(default=4, ge=1, le=256)
    chat_history_max_turns: int = Field(default=6, ge=0, le=50)
    chat_history_max_chars: int = Field(default=8000, ge=0, le=100_000)
    code_context_max_tokens: int = Field(default=12000, ge=256)
    code_context_max_chars: int = Field(default=60000, ge=1000)
    agent_tool_prompt_reserve_tokens: int = Field(default=4096, ge=0)
    agent_tool_context_max_chars: int = Field(default=12000, ge=1000)
    agent_runtime: str = Field(default="legacy", pattern="^(legacy|pydantic_ai)$")
    agent_runtime_retries: int = Field(default=1, ge=0, le=3)
    agent_structured_output: bool = False

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
    office_com_timeout_seconds: int = Field(default=300, ge=1)
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

    analysis_upload_max_bytes: int = Field(default=500_000_000, ge=1)
    analysis_max_rows: int = Field(default=5_000_000, ge=1)
    analysis_max_groups: int = Field(default=5000, ge=1)
    analysis_result_max_rows: int = Field(default=5000, ge=1)
    analysis_sample_rows: int = Field(default=20, ge=1, le=500)
    analysis_explanation_max_chars: int = Field(default=12000, ge=1000)
    analysis_retention_hours: int = Field(default=168, ge=1, le=8760)
    analysis_job_timeout_seconds: int = Field(default=1800, ge=1)
    analysis_profile_timeout_seconds: int = Field(default=3600, ge=1)
    analysis_profile_sample_rows: int = Field(default=50, ge=1, le=1000)
    analysis_profile_max_columns: int = Field(default=500, ge=1, le=5000)
    analysis_join_max_sources: int = Field(default=8, ge=2, le=20)
    analysis_advanced_collect_max_rows: int = Field(default=500_000, ge=1)
    analysis_chart_max_points: int = Field(default=5000, ge=10)
    analysis_export_max_rows: int = Field(default=100_000, ge=1)
    analysis_plan_schema_max_chars: int = Field(default=16_000, ge=1000)
    analysis_hybrid_context_max_chars: int = Field(default=24_000, ge=2000)
    analysis_intent_flow_enabled: bool = False
    analysis_recipes_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
