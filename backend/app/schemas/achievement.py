from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import TimestampedModel


class AchievementCreate(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    taxonomy_id: str | None = None
    abstract: str = Field(default="", max_length=20_000)
    technical_principle: str = Field(default="", max_length=20_000)
    application_scenarios: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    maturity_level: int | None = Field(default=None, ge=1, le=9)
    cost_description: str = ""
    advantages: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    structured_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return " ".join(value.split())


class AchievementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=500)
    taxonomy_id: str | None = None
    abstract: str | None = None
    technical_principle: str | None = None
    application_scenarios: list[str] | None = None
    organizations: list[str] | None = None
    maturity_level: int | None = Field(default=None, ge=1, le=9)
    cost_description: str | None = None
    advantages: list[str] | None = None
    limitations: list[str] | None = None
    metrics: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    structured_data: dict[str, Any] | None = None


class AchievementResponse(TimestampedModel):
    organization_id: str | None
    taxonomy_id: str | None
    title: str
    normalized_title: str
    abstract: str
    technical_principle: str
    application_scenarios: list[str]
    organizations: list[str]
    maturity_level: int | None
    cost_description: str
    advantages: list[str]
    limitations: list[str]
    metrics: list[dict[str, Any]]
    tags: list[str]
    structured_data: dict[str, Any]
    evidence_score: float
    status: str
    version: int
    qdrant_indexed: bool


class AchievementDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject|archive)$")
    comment: str = Field(default="", max_length=2_000)
