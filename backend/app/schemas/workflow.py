from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import TimestampedModel


class AchievementEvaluationResponse(TimestampedModel):
    achievement_id: str
    rubric_id: str
    agent_run_id: str | None
    evaluation_version: int
    status: str
    dimension_scores: dict[str, float]
    dimension_details: dict[str, str]
    total_score: float
    level: str
    summary: str
    strengths: list[str]
    risks: list[str]
    evidence: list[dict[str, Any]]
    missing_information: list[str]
    recommendations: list[str]
    confidence: float
    model_name: str
    rubric_snapshot: dict[str, Any]
    verification_status: str


class RequirementRefinementResponse(TimestampedModel):
    requirement_id: str
    agent_run_id: str | None
    refinement_version: int
    status: str
    profile: dict[str, Any]
    completeness_score: float
    confirmed: bool


class MatchRunSummary(TimestampedModel):
    requirement_id: str
    agent_run_id: str | None
    status: str
    summary: str
    confidence: float
    candidate_count: int
    top_score: float | None = None


class MatchItemResponse(TimestampedModel):
    match_id: str
    achievement_id: str
    achievement_title: str
    rank: int
    final_score: float
    hard_constraints_passed: bool
    eligibility: str
    constraint_checks: list[dict[str, Any]]
    score_kind: str
    dimension_scores: dict[str, float]
    hard_constraint_violations: list[str]
    reasons: list[str]
    gaps: list[str]
    risks: list[str]
    evidence: list[dict[str, Any]]
    recommendation: str
    decision_status: str
    decided_at: datetime | None


class MatchRunDetail(MatchRunSummary):
    rubric_snapshot: dict[str, Any]
    items: list[MatchItemResponse]


class MatchDecision(BaseModel):
    decision: str = Field(pattern="^(pending|shortlisted|rejected|adopted)$")
    comment: str = Field(default="", max_length=5_000)


class AchievementWorkflowSummary(BaseModel):
    achievement_id: str
    evaluation: AchievementEvaluationResponse | None = None


class RequirementWorkflowSummary(BaseModel):
    requirement_id: str
    latest_refinement_status: str | None = None
    latest_match_id: str | None = None
    latest_match_status: str | None = None
    latest_match_score: float | None = None
    latest_match_candidate_count: int = 0
    latest_match_at: datetime | None = None
