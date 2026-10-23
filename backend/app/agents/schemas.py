from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


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
    generation_mode: str = "llm"
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

    @field_validator("hard_constraints")
    @classmethod
    def normalize_trl_minimum(cls, constraints: dict[str, Any]) -> dict[str, Any]:
        """Map explicit TRL lower bounds to the field used by retrieval filters.

        Leave ambiguous maturity language and upper/exact bounds untouched so
        they remain unknown, rather than silently changing their meaning.
        """
        result = dict(constraints)
        canonical = result.get("min_maturity_level")
        if canonical is not None and (
            isinstance(canonical, bool) or not isinstance(canonical, int) or not 1 <= canonical <= 9
        ):
            return result
        for key, value in constraints.items():
            if key.casefold().replace(" ", "") not in {
                "trl",
                "技术就绪度",
                "trl成熟度",
                "成熟度等级",
                "技术成熟度",
            }:
                continue
            match = (
                re.fullmatch(
                    r"(?:TRL\s*)?(?:>=|≥|至少|不低于)\s*([1-9])\s*级?",
                    value.strip(),
                    re.IGNORECASE,
                )
                if isinstance(value, str)
                else None
            )
            if match:
                result["min_maturity_level"] = max(
                    int(match.group(1)), result.get("min_maturity_level") or 1
                )
                del result[key]
        return result


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


class IntrinsicAchievementEvaluation(BaseModel):
    generation_mode: str = "llm"
    overall_score: float = Field(ge=0, le=100)
    level: str = Field(max_length=50)
    dimension_scores: dict[str, float]
    dimension_details: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class IntrinsicScorecard(BaseModel):
    overall_score: float = Field(ge=0, le=100)
    level: str = Field(max_length=50)
    innovation: float = Field(ge=0, le=100)
    effectiveness: float = Field(ge=0, le=100)
    maturity: float = Field(ge=0, le=100)
    evidence_quality: float = Field(ge=0, le=100)
    promotion_value: float = Field(ge=0, le=100)
    implementation_feasibility: float = Field(ge=0, le=100)
    summary: str = Field(max_length=500)
    confidence: float = Field(ge=0, le=1)


class ConstraintCheck(BaseModel):
    constraint: str
    status: Literal["satisfied", "violated", "unknown"]
    reason: str
    chunk_id: str = ""
    quote: str = ""
    # rule: deterministic field comparison; evidence: model judgement bound to a
    # verbatim quote that the server verified inside an authoritative PG chunk.
    method: Literal["rule", "evidence"] = "rule"


class ConstraintVerdict(BaseModel):
    constraint: str
    status: Literal["satisfied", "violated", "unknown"]
    chunk_id: str = ""
    quote: str = Field(default="", max_length=400)
    reason: str = Field(default="", max_length=300)


class ConstraintVerdictBatch(BaseModel):
    verdicts: list[ConstraintVerdict] = Field(default_factory=list, max_length=20)


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
    constraint_checks: list[ConstraintCheck] = Field(default_factory=list)
    eligibility: Literal["satisfied", "violated", "unknown"] = "unknown"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    score_kind: str = "retrieval_relevance"


class MatchAssessmentBatch(BaseModel):
    generation_mode: str = "llm"
    results: list[MatchAssessment] = Field(default_factory=list, max_length=20)
    overall_summary: str = ""
    retrieval_warnings: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    decision: Literal["passed", "revision_required", "human_review_required", "rejected"]
    confidence: float = Field(ge=0, le=1)
    unsupported_claims: list[str] = Field(default_factory=list)
    citation_problems: list[str] = Field(default_factory=list)
    constraint_violations: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    review_reason: str = ""
