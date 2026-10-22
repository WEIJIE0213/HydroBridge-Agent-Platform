from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import TimestampedModel


class RequirementCreate(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    raw_input: str = Field(min_length=5, max_length=20_000)


class RequirementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    raw_input: str | None = Field(default=None, min_length=5, max_length=20_000)
    normalized_description: str | None = None
    scenario: str | None = None
    problem: str | None = None
    objectives: list[str] | None = None
    hard_constraints: dict[str, Any] | None = None
    soft_preferences: dict[str, Any] | None = None
    known_conditions: dict[str, Any] | None = None
    search_terms: list[str] | None = None
    missing_fields: list[str] | None = None
    completeness_score: float | None = Field(default=None, ge=0, le=1)
    status: str | None = None


class RequirementResponse(TimestampedModel):
    organization_id: str | None
    created_by: str
    title: str
    raw_input: str
    normalized_description: str
    scenario: str
    problem: str
    objectives: list[str]
    hard_constraints: dict[str, Any]
    soft_preferences: dict[str, Any]
    known_conditions: dict[str, Any]
    search_terms: list[str]
    missing_fields: list[str]
    completeness_score: float
    status: str
