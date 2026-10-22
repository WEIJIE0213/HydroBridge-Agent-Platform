from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, DatabaseSession
from app.db.models.domain import Achievement, Requirement
from app.db.models.runtime import AgentRun, ApprovalTask

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("")
async def overview(
    session: DatabaseSession,
    user: CurrentUser,
) -> dict[str, object]:
    achievement_stats = (
        await session.execute(
            select(
                func.count(Achievement.id),
                func.sum(case((Achievement.status == "approved", 1), else_=0)),
                func.avg(Achievement.evidence_score),
            )
        )
    ).one()
    run_filters = [] if user.is_superuser else [AgentRun.user_id == user.id]
    run_rows = (
        await session.execute(
            select(AgentRun.status, func.count(AgentRun.id))
            .where(*run_filters)
            .group_by(AgentRun.status)
        )
    ).all()
    requirement_count = await session.scalar(
        select(func.count(Requirement.id)).where(Requirement.created_by == user.id)
    )
    approval_count = await session.scalar(
        select(func.count(ApprovalTask.id)).where(ApprovalTask.status == "pending")
    )
    return {
        "achievements": {
            "total": int(achievement_stats[0] or 0),
            "approved": int(achievement_stats[1] or 0),
            "average_evidence_score": round(float(achievement_stats[2] or 0), 3),
        },
        "runs": {status: int(count) for status, count in run_rows},
        "requirements": int(requirement_count or 0),
        "pending_approvals": int(approval_count or 0),
    }
