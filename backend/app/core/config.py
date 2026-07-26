from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
        "mysql+asyncmy://hydrobridge:change-me-local@localhost:3307/"
        "hydrobridge?charset=utf8mb4"
    )
    redis_runtime_url: str = "redis://localhost:6380/0"
    redis_cache_url: str = "redis://localhost:6381/0"
    celery_broker_url: str = "redis://localhost:6380/1"
    celery_result_backend: str = "redis://localhost:6380/2"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "hydrobridge_knowledge"
    knowledge_index_version: int = Field(default=2, ge=1)
    embedding_backend: Literal["auto", "bge_m3", "hash"] = "auto"
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

    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_timeout_seconds: int = Field(default=90, ge=10, le=600)

    tavily_api_key: str | None = None
    tavily_search_depth: Literal["basic", "advanced"] = "advanced"
    tavily_max_results: int = Field(default=8, ge=1, le=20)

    agent_max_iterations: int = Field(default=12, ge=2, le=30)
    agent_max_parallel_tools: int = Field(default=4, ge=1, le=10)
    agent_run_timeout_seconds: int = Field(default=600, ge=60, le=3600)
    agent_daily_token_budget: int = Field(default=1_000_000, ge=1_000)
    agent_execution_mode: Literal["inline", "celery"] = "celery"

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
