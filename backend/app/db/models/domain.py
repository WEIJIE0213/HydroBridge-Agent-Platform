from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Taxonomy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "taxonomies"
    __table_args__ = (UniqueConstraint("parent_id", "code", name="uq_taxonomies_parent_code"),)

    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("taxonomies.id", ondelete="SET NULL")
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Achievement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "achievements"
    __table_args__ = (
        Index("ix_achievements_org_status_updated", "organization_id", "status", "updated_at"),
        Index("ix_achievements_taxonomy_status", "taxonomy_id", "status"),
        UniqueConstraint("organization_id", "normalized_title", name="uq_achievement_org_title"),
    )

    organization_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL")
    )
    taxonomy_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("taxonomies.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(500), nullable=False)
    abstract: Mapped[str] = mapped_column(Text, default="")
    technical_principle: Mapped[str] = mapped_column(Text, default="")
    application_scenarios: Mapped[list[str]] = mapped_column(JSON, default=list)
    organizations: Mapped[list[str]] = mapped_column(JSON, default=list)
    maturity_level: Mapped[int | None] = mapped_column(Integer)
    cost_description: Mapped[str] = mapped_column(Text, default="")
    advantages: Mapped[list[str]] = mapped_column(JSON, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    structured_data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    evidence_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="candidate", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    search_indexed: Mapped[bool] = mapped_column(Boolean, default=False)


class AchievementSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "achievement_sources"
    __table_args__ = (
        Index("ix_achievement_sources_achievement", "achievement_id", "source_type"),
        UniqueConstraint("achievement_id", "canonical_url_hash", name="uq_achievement_source_url"),
    )

    achievement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(50), default="web")
    title: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str | None] = mapped_column(Text)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher: Mapped[str] = mapped_column(String(300), default="")
    authority_level: Mapped[str] = mapped_column(String(30), default="unknown")
    is_primary_source: Mapped[bool] = mapped_column(Boolean, default=False)
    credibility_score: Mapped[float] = mapped_column(Float, default=0)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)


class Requirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (
        Index("ix_requirements_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_requirements_user_created", "created_by", "created_at"),
    )

    organization_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_description: Mapped[str] = mapped_column(Text, default="")
    scenario: Mapped[str] = mapped_column(String(200), default="")
    problem: Mapped[str] = mapped_column(Text, default="")
    objectives: Mapped[list[str]] = mapped_column(JSON, default=list)
    hard_constraints: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    soft_preferences: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    known_conditions: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    search_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    completeness_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")


class EvaluationRubric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_rubrics"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_rubric_name_version"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    taxonomy_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("taxonomies.id", ondelete="SET NULL")
    )
    dimensions: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AchievementEvaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "achievement_evaluations"
    __table_args__ = (Index("ix_evaluations_achievement_created", "achievement_id", "created_at"),)

    achievement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False
    )
    rubric_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_rubrics.id", ondelete="RESTRICT"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    evaluation_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    dimension_scores: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    dimension_details: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    level: Mapped[str] = mapped_column(String(50), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    missing_information: Mapped[list[str]] = mapped_column(JSON, default=list)
    recommendations: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    model_name: Mapped[str] = mapped_column(String(200), default="")
    rubric_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    verification_status: Mapped[str] = mapped_column(String(30), default="pending")


class RequirementRefinement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "requirement_refinements"
    __table_args__ = (
        Index("ix_requirement_refinements_requirement_created", "requirement_id", "created_at"),
    )

    requirement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    refinement_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    profile: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    completeness_score: Mapped[float] = mapped_column(Float, default=0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class MatchRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (Index("ix_matches_requirement_created", "requirement_id", "created_at"),)

    requirement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="running")
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0)
    rubric_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)


class MatchItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "match_items"
    __table_args__ = (
        UniqueConstraint("match_id", "achievement_id", name="uq_match_item_achievement"),
        Index("ix_match_items_match_rank", "match_id", "rank"),
    )

    match_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    achievement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    dense_score: Mapped[float] = mapped_column(Float, default=0)
    sparse_score: Mapped[float] = mapped_column(Float, default=0)
    fusion_score: Mapped[float] = mapped_column(Float, default=0)
    reranker_score: Mapped[float] = mapped_column(Float, default=0)
    business_score: Mapped[float] = mapped_column(Float, default=0)
    final_score: Mapped[float] = mapped_column(Float, default=0)
    hard_constraints_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    eligibility: Mapped[str] = mapped_column(String(20), default="unknown")
    constraint_checks: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    score_kind: Mapped[str] = mapped_column(String(40), default="retrieval_relevance")
    dimension_scores: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    hard_constraint_violations: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    gaps: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    decision_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (Index("ix_feedback_target", "target_type", "target_id"),)

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    comment: Mapped[str] = mapped_column(Text, default="")
    corrections: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
