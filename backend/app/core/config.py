from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "HydroBridge AI"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:3000"

    database_url: str = (
        "postgresql+asyncpg://hydrobridge:change-me-local@localhost:5433/hydrobridge"
    )
    redis_runtime_url: str = "redis://localhost:6380/0"
    redis_cache_url: str = "redis://localhost:6381/0"
    celery_broker_url: str = "redis://localhost:6380/1"
    celery_result_backend: str = "redis://localhost:6380/2"

    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_api_key: str | None = None
    elasticsearch_index: str = "hydrobridge-chunks-v3"
    elasticsearch_analyzer: Literal["ik_max_word", "cjk"] = "ik_max_word"
    retrieval_backend: Literal["elasticsearch", "local"] = "elasticsearch"
    retrieval_allow_fallback: bool = False
    retrieval_candidates: int = Field(default=60, ge=10, le=200)
    # HNSW graph parameters for new indexes. int8_hnsw with m=16/ef_construction=100
    # equals the Elasticsearch 8.x default, so existing indexes keep their behavior.
    elasticsearch_vector_index_type: Literal["hnsw", "int8_hnsw"] = "int8_hnsw"
    elasticsearch_hnsw_m: int = Field(default=16, ge=4, le=64)
    elasticsearch_hnsw_ef_construction: int = Field(default=100, ge=16, le=1000)
    # kNN query breadth: num_candidates = k * factor per shard (search-time ef).
    knn_num_candidates_factor: int = Field(default=3, ge=1, le=20)
    # Number of top matched achievements whose unresolved constraints are checked
    # against retrieved original evidence by the model. 0 disables the step.
    match_evidence_verify_limit: int = Field(default=5, ge=0, le=10)
    chunk_size: int = Field(default=700, ge=100, le=2000)
    chunk_overlap: int = Field(default=100, ge=0, le=300)
    rag_max_rounds: int = Field(default=2, ge=1, le=2)
    knowledge_index_version: int = Field(default=3, ge=1)
    embedding_backend: Literal["auto", "bge_m3", "bge_service", "hash"] = "auto"
    bge_service_url: str = "http://localhost:8081"
    bge_service_timeout: int = Field(default=180, ge=10, le=600)
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = Field(default=1024, ge=128, le=4096)
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "hydrobridge"
    minio_secret_key: str = "change-me-minio"
    minio_secure: bool = False
    minio_bucket_documents: str = "hydrobridge-documents"
    minio_bucket_artifacts: str = "hydrobridge-artifacts"

    jwt_secret_key: str = "development-only-secret-change-before-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    bootstrap_admin_email: str = "admin@hydrobridge.cn"
    bootstrap_admin_password: str = "ChangeMe123!"
    bootstrap_seed_data: bool = True
    bootstrap_seed_path: str = "/seed-data/achievements.json"

    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CONTINUUM_LLM_API_KEY", "LLM_API_KEY"),
    )
    llm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CONTINUUM_LLM_BASE_URL", "LLM_BASE_URL"),
    )
    llm_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CONTINUUM_LLM_MODEL", "LLM_MODEL"),
    )
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_timeout_seconds: int = Field(default=180, ge=10, le=600)
    llm_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="high",
        validation_alias=AliasChoices(
            "CONTINUUM_LLM_REASONING_EFFORT",
            "LLM_REASONING_EFFORT",
        ),
    )
    llm_thinking: bool = Field(
        default=False,
        validation_alias=AliasChoices("CONTINUUM_LLM_THINKING", "LLM_THINKING"),
    )
    llm_json_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices("CONTINUUM_LLM_JSON_MODE", "LLM_JSON_MODE"),
    )

    tavily_api_key: str | None = None
    tavily_search_depth: Literal["basic", "advanced"] = "advanced"
    tavily_max_results: int = Field(default=8, ge=1, le=20)

    research_ingestion_review_threshold: float = Field(default=60, ge=0, le=100)
    research_ingestion_auto_threshold: float = Field(default=80, ge=0, le=100)
    research_ingestion_review_limit: int = Field(default=5, ge=1, le=10)

    agent_max_iterations: int = Field(default=12, ge=2, le=30)
    agent_max_parallel_tools: int = Field(default=4, ge=1, le=10)
    agent_run_timeout_seconds: int = Field(default=600, ge=60, le=3600)
    agent_daily_token_budget: int = Field(default=1_000_000, ge=1_000)
    agent_execution_mode: Literal["inline", "celery"] = "celery"
    context_history_token_budget: int = Field(default=6_000, ge=1_000, le=32_000)
    context_summary_token_budget: int = Field(default=1_000, ge=200, le=4_000)
    context_recent_messages: int = Field(default=8, ge=2, le=40)
    context_summary_batch_tokens: int = Field(default=6_000, ge=1_000, le=16_000)

    otel_exporter_otlp_endpoint: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "hydrobridge-ai"

    @field_validator("api_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_research_ingestion_thresholds(self) -> Settings:
        if self.context_summary_token_budget >= self.context_history_token_budget:
            raise ValueError("CONTEXT_SUMMARY_TOKEN_BUDGET must be below history budget")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.research_ingestion_auto_threshold <= self.research_ingestion_review_threshold:
            raise ValueError(
                "RESEARCH_INGESTION_AUTO_THRESHOLD must be greater than "
                "RESEARCH_INGESTION_REVIEW_THRESHOLD"
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_url.rstrip("/")]

    @property
    def model_is_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def tavily_is_configured(self) -> bool:
        return bool(self.tavily_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
