from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class SupervisorDecision(BaseModel):
    task_type: Literal[
        "research",
        "requirement_analysis",
        "evaluation",
        "matching",
        "qa",
    ]
    plan: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(max_length=1_000)


class RequirementProfile(BaseModel):
    title: str
    normalized_description: str
    scenario: str
    problem: str
    objectives: list[str] = Field(default_factory=list)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    soft_preferences: dict[str, Any] = Field(default_factory=dict)
    known_conditions: dict[str, Any] = Field(default_factory=dict)
    missing_critical_fields: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    completeness_score: float = Field(ge=0, le=1)
    requires_clarification: bool = False
    clarification_question: str = ""


class ResearchPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    target_information: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)


class SourceCandidate(BaseModel):
    title: str
    url: HttpUrl
    publisher: str = ""
    content: str = ""
    source_type: str = "web"
    authority_level: Literal[
        "official",
        "academic",
        "industry",
        "enterprise",
        "unknown",
    ] = "unknown"
    credibility_score: float = Field(default=0, ge=0, le=1)


class EvidenceClaim(BaseModel):
    field: str
    claim: str
    evidence_text: str
    source_url: str
    support_type: Literal["direct", "indirect", "conflict", "missing"]
    confidence: float = Field(ge=0, le=1)


class CuratedAchievement(BaseModel):
    title: str
    normalized_title: str
    organizations: list[str] = Field(default_factory=list)
    abstract: str = ""
    technical_principle: str = ""
    application_scenarios: list[str] = Field(default_factory=list)
    maturity_level: int | None = Field(default=None, ge=1, le=9)
    advantages: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceClaim] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    evidence_score: float = Field(default=0, ge=0, le=1)


class CuratedAchievementBatch(BaseModel):
    achievements: list[CuratedAchievement] = Field(default_factory=list, max_length=10)
    rejected_sources: list[dict[str, str]] = Field(default_factory=list)


class MatchAssessment(BaseModel):
    achievement_id: str
    title: str
    rank: int = Field(ge=1)
    final_score: float = Field(ge=0, le=100)
    scenario_fit: float = Field(ge=0, le=100)
    effectiveness: float = Field(ge=0, le=100)
    maturity: float = Field(ge=0, le=100)
    evidence_quality: float = Field(ge=0, le=100)
    cost_fit: float = Field(ge=0, le=100)
    implementation_risk: float = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    cited_source_ids: list[str] = Field(default_factory=list)


class MatchAssessmentBatch(BaseModel):
    results: list[MatchAssessment] = Field(default_factory=list, max_length=20)
    overall_summary: str = ""


class VerificationResult(BaseModel):
    decision: Literal["passed", "revision_required", "human_review_required", "rejected"]
    confidence: float = Field(ge=0, le=1)
    unsupported_claims: list[str] = Field(default_factory=list)
    citation_problems: list[str] = Field(default_factory=list)
    constraint_violations: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    review_reason: str = ""
