from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.domain import (
    Achievement,
    AchievementEvaluation,
    MatchItem,
    MatchRun,
    Requirement,
    RequirementRefinement,
)


class WorkflowResultStore:
    """Persist each completed action independently of the enclosing Agent run."""

    async def persist(
        self,
        session: AsyncSession,
        context: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        action = context.get("domain_action")
        verification = result.get("verification", {})
        if action == "achievement_evaluation":
            record = await session.scalar(
                select(AchievementEvaluation)
                .where(AchievementEvaluation.id == context.get("evaluation_id"))
                .with_for_update()
            )
            if record is not None and record.status == "completed":
                return
            evaluation = result.get("achievement_evaluation", {})
            if record is None or not evaluation:
                return
            record.status = "completed"
            record.dimension_scores = evaluation.get("dimension_scores", {})
            record.dimension_details = evaluation.get("dimension_details", {})
            record.total_score = float(evaluation.get("overall_score", 0))
            record.level = str(evaluation.get("level", ""))
            record.summary = str(evaluation.get("summary", ""))
            record.strengths = list(evaluation.get("strengths", []))
            record.risks = list(evaluation.get("risks", []))
            record.evidence = list(evaluation.get("evidence", []))
            record.missing_information = list(evaluation.get("missing_information", []))
            record.recommendations = list(evaluation.get("recommendations", []))
            record.confidence = float(evaluation.get("confidence", 0))
            record.model_name = (
                settings.llm_model
                if evaluation.get("generation_mode", "llm") == "llm"
                else "deterministic-fallback"
            )
            record.verification_status = str(verification.get("decision", "pending"))
            return

        if action == "requirement_refinement":
            refinement = await session.scalar(
                select(RequirementRefinement)
                .where(RequirementRefinement.id == context.get("refinement_id"))
                .with_for_update()
            )
            if refinement is not None and refinement.status == "completed":
                return
            requirement = await session.get(Requirement, context.get("requirement_id"))
            profile = result.get("requirement_profile", {})
            if refinement is None or requirement is None or not profile:
                return
            refinement.status = "completed"
            refinement.profile = profile
            refinement.completeness_score = float(profile.get("completeness_score", 0))
            refinement.confirmed = True
            self._apply_requirement_profile(requirement, profile, result)
            return

        if action != "requirement_matching":
            return
        match = await session.scalar(
            select(MatchRun).where(MatchRun.id == context.get("match_id")).with_for_update()
        )
        if match is not None and match.status == "completed":
            return
        if match is None:
            return
        profile = result.get("requirement_profile", {})
        requirement = await session.get(Requirement, match.requirement_id)
        if requirement is not None and profile:
            self._apply_requirement_profile(requirement, profile, result)
            match.rubric_snapshot = {
                **match.rubric_snapshot,
                "hard_constraints": profile.get("hard_constraints", {}),
            }
        assessments = list(result.get("assessment_results", []))
        persisted = 0
        for assessment in assessments:
            achievement_id = str(assessment.get("achievement_id", ""))
            achievement = await session.get(Achievement, achievement_id)
            if achievement is None:
                continue
            risk_value = float(assessment.get("implementation_risk", 0))
            dimensions = {
                "scenario_fit": float(assessment.get("scenario_fit", 0)),
                "effectiveness": float(assessment.get("effectiveness", 0)),
                "maturity": float(assessment.get("maturity", 0)),
                "evidence_quality": float(assessment.get("evidence_quality", 0)),
                "cost_fit": float(assessment.get("cost_fit", 0)),
                "implementation_risk": risk_value,
            }
            final_score = float(assessment.get("final_score", 0))
            session.add(
                MatchItem(
                    match_id=match.id,
                    achievement_id=achievement.id,
                    rank=int(assessment.get("rank", persisted + 1)),
                    final_score=final_score,
                    business_score=final_score,
                    hard_constraints_passed=assessment.get("eligibility") == "satisfied",
                    eligibility=assessment.get("eligibility", "unknown"),
                    constraint_checks=assessment.get("constraint_checks", []),
                    score_kind=assessment.get("score_kind", "retrieval_relevance"),
                    dimension_scores=dimensions,
                    hard_constraint_violations=[
                        c["reason"]
                        for c in assessment.get("constraint_checks", [])
                        if c["status"] == "violated"
                    ],
                    reasons=list(assessment.get("reasons", [])),
                    gaps=list(assessment.get("gaps", [])),
                    risks=list(assessment.get("risks", [])),
                    evidence=assessment.get("evidence", []),
                    recommendation=(
                        "存在不满足的硬约束，当前条件下不推荐"
                        if assessment.get("eligibility") == "violated"
                        else "硬约束尚有未知项，补齐证据后再判断适用性"
                        if assessment.get("eligibility", "unknown") == "unknown"
                        else "已核验的结构化约束满足，仍需专家开展工程适配论证"
                    ),
                )
            )
            persisted += 1
        match.status = "completed"
        match.summary = str(result.get("final_answer", ""))
        match.confidence = float(verification.get("confidence", 0))
        match.candidate_count = persisted
        requirement = await session.get(Requirement, match.requirement_id)
        if requirement is not None:
            requirement.status = "matched"

    @staticmethod
    def _apply_requirement_profile(requirement, profile, result):
        requirement.raw_input = str(result.get("user_message") or requirement.raw_input)
        requirement.title = str(profile.get("title") or requirement.title)[:300]
        requirement.normalized_description = str(
            profile.get("normalized_description", requirement.raw_input)
        )
        requirement.scenario = str(profile.get("scenario", ""))[:200]
        requirement.problem = str(profile.get("problem", ""))
        requirement.objectives = list(profile.get("objectives", []))
        requirement.hard_constraints = dict(profile.get("hard_constraints", {}))
        requirement.soft_preferences = dict(profile.get("soft_preferences", {}))
        requirement.known_conditions = dict(profile.get("known_conditions", {}))
        requirement.search_terms = list(profile.get("search_terms", []))
        requirement.missing_fields = list(profile.get("missing_critical_fields", []))
        requirement.completeness_score = float(profile.get("completeness_score", 0))
        requirement.status = "ready" if requirement.completeness_score >= 0.6 else "draft"
