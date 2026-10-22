from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.state import AgentState
from app.db.models.domain import (
    Achievement,
    AchievementEvaluation,
    EvaluationRubric,
    MatchRun,
    Requirement,
    RequirementRefinement,
)
from app.db.models.runtime import AgentRun
from app.db.session import AsyncSessionFactory


async def create_evaluation(
    session: AsyncSession, achievement: Achievement
) -> AchievementEvaluation:
    await session.scalar(
        select(Achievement).where(Achievement.id == achievement.id).with_for_update()
    )
    rubric = await session.scalar(
        select(EvaluationRubric)
        .where(
            EvaluationRubric.name == "水利科技成果自身质量评价",
            EvaluationRubric.enabled.is_(True),
        )
        .order_by(EvaluationRubric.version.desc())
        .limit(1)
    )
    if rubric is None:
        rubric = EvaluationRubric(
            name="水利科技成果自身质量评价",
            version=1,
            dimensions=[
                {"key": "innovation", "name": "创新性", "weight": 0.20},
                {"key": "effectiveness", "name": "技术有效性", "weight": 0.20},
                {"key": "maturity", "name": "工程成熟度", "weight": 0.15},
                {"key": "evidence_quality", "name": "证据充分性", "weight": 0.20},
                {"key": "promotion_value", "name": "推广复制价值", "weight": 0.15},
                {"key": "implementation_feasibility", "name": "经济与实施可行性", "weight": 0.10},
            ],
        )
        session.add(rubric)
        await session.flush()
    version = await session.scalar(
        select(func.max(AchievementEvaluation.evaluation_version)).where(
            AchievementEvaluation.achievement_id == achievement.id
        )
    )
    record = AchievementEvaluation(
        achievement_id=achievement.id,
        rubric_id=rubric.id,
        evaluation_version=(version or 0) + 1,
        status="queued",
        rubric_snapshot={
            "name": rubric.name, "version": rubric.version, "dimensions": rubric.dimensions,
        },
    )
    session.add(record)
    await session.flush()
    return record


async def create_refinement(
    session: AsyncSession, requirement: Requirement
) -> RequirementRefinement:
    await session.scalar(
        select(Requirement).where(Requirement.id == requirement.id).with_for_update()
    )
    version = await session.scalar(
        select(func.max(RequirementRefinement.refinement_version)).where(
            RequirementRefinement.requirement_id == requirement.id
        )
    )
    record = RequirementRefinement(
        requirement_id=requirement.id, refinement_version=(version or 0) + 1, status="queued"
    )
    session.add(record)
    await session.flush()
    return record


async def create_match(session: AsyncSession, requirement: Requirement) -> MatchRun:
    record = MatchRun(
        requirement_id=requirement.id,
        status="queued",
        rubric_snapshot={
            "dimensions": [
                "scenario_fit", "effectiveness", "maturity", "evidence_quality",
                "cost_fit", "implementation_risk",
            ],
            "hard_constraints": requirement.hard_constraints,
        },
    )
    session.add(record)
    await session.flush()
    return record


async def bind_workflow(state: AgentState, task_type: str, message: str, context: dict) -> dict:
    """Bind chat workflows to the same records used by the management pages.

    Persist the binding before executing a workflow, so failures and interruptions
    update that same record. Re-entering a run reuses its binding.
    """
    async with AsyncSessionFactory() as session:
        run = await session.scalar(
            select(AgentRun).where(AgentRun.id == state["run_id"]).with_for_update()
        )
        # The graph can also be invoked without the HTTP/runtime persistence layer.
        if run is None:
            return context
        if run.user_id != state["user_id"]:
            raise ValueError("无权关联该运行的业务记录")
        saved_context = dict(run.input_payload.get("context", {}))
        action = {
            "evaluation": "achievement_evaluation",
            "requirement_analysis": "requirement_refinement",
            "matching": "requirement_matching",
        }.get(task_type)
        record = None
        if action and saved_context.get("domain_action") == action:
            context = {**context, **saved_context}
        elif task_type == "evaluation":
            achievement = await session.get(Achievement, context["achievement_id"])
            if achievement is None:
                raise ValueError("待评价成果不存在")
            record = await create_evaluation(session, achievement)
            context = {**context, "domain_action": action, "evaluation_id": record.id}
        elif task_type in {"requirement_analysis", "matching"}:
            requirement_id = context.get("requirement_id")
            if requirement_id:
                requirement = await session.scalar(
                    select(Requirement)
                    .where(Requirement.id == requirement_id, Requirement.created_by == run.user_id)
                    .with_for_update()
                )
                if requirement is None:
                    raise ValueError("需求不存在或无权修改")
            else:
                requirement = Requirement(
                    created_by=run.user_id,
                    organization_id=state.get("organization_id"),
                    title=message.strip()[:100] or "聊天创建的工程需求",
                    raw_input=message,
                )
                session.add(requirement)
                await session.flush()
            if task_type == "matching":
                record = await create_match(session, requirement)
                context = {**context, "match_id": record.id}
                requirement.status = "matching"
            else:
                record = await create_refinement(session, requirement)
                context = {**context, "refinement_id": record.id}
                requirement.status = "refining"
            context.update(domain_action=action, requirement_id=requirement.id)
        if record is not None:
            record.agent_run_id = run.id
            record.status = "running"
        run.task_type = task_type
        run.input_payload = {**run.input_payload, "context": context}
        await session.commit()
        return context
