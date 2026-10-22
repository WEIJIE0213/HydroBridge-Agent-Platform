from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import defer

from app.api.deps import CurrentUser, DatabaseSession, Services, require_roles
from app.api.routes.workflow_helpers import dispatch_agent_run
from app.db.models.domain import Achievement, AchievementEvaluation
from app.db.models.identity import User
from app.db.models.knowledge import OutboxEvent
from app.schemas.achievement import (
    AchievementCreate,
    AchievementDecision,
    AchievementResponse,
    AchievementUpdate,
)
from app.schemas.common import Page
from app.schemas.workflow import (
    AchievementEvaluationResponse,
    AchievementWorkflowSummary,
)
from app.services.workflow_records import create_evaluation

router = APIRouter(prefix="/achievements", tags=["achievements"])
Reviewer = Annotated[User, Depends(require_roles("admin", "expert", "operator"))]


def normalize_title(title: str) -> str:
    return re.sub(r"[\s《》“”\"'（）()]+", "", title).casefold()


def catalog_access_filter(user: User):
    public = and_(Achievement.organization_id.is_(None), Achievement.status == "approved")
    return (
        or_(public, Achievement.organization_id == user.organization_id)
        if (user.organization_id is not None)
        else public
    )


def require_catalog_write(achievement: Achievement, user: User) -> None:
    if not user.is_superuser and (
        user.organization_id is None or achievement.organization_id != user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权修改其他组织或公共成果")


@router.get("", response_model=Page[AchievementResponse])
async def list_achievements(
    session: DatabaseSession,
    user: CurrentUser,
    query: str | None = Query(default=None, max_length=200),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[AchievementResponse]:
    filters = []
    if not user.is_superuser:
        filters.append(catalog_access_filter(user))
    if status_filter:
        filters.append(Achievement.status == status_filter)
    if query:
        filters.append(
            or_(
                Achievement.title.like(f"%{query}%"),
                Achievement.abstract.like(f"%{query}%"),
            )
        )
    count = await session.scalar(select(func.count()).select_from(Achievement).where(*filters))
    result = await session.execute(
        select(Achievement)
        .options(defer(Achievement.structured_data))
        .where(*filters)
        .order_by(Achievement.updated_at.desc(), Achievement.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    # Source texts can contain entire research reports. List pages need metadata only.
    items = [
        AchievementResponse.model_validate(
            {
                **{
                    column.name: getattr(item, column.name)
                    for column in Achievement.__table__.columns
                    if column.name != "structured_data"
                },
                "structured_data": {},
            }
        )
        for item in result.scalars()
    ]
    return Page(items=items, total=count or 0, page=page, page_size=page_size)


@router.post("", response_model=AchievementResponse, status_code=status.HTTP_201_CREATED)
async def create_achievement(
    payload: AchievementCreate,
    session: DatabaseSession,
    user: CurrentUser,
) -> Achievement:
    achievement = Achievement(
        organization_id=user.organization_id,
        normalized_title=normalize_title(payload.title),
        status="candidate",
        **payload.model_dump(),
    )
    session.add(achievement)
    await session.commit()
    await session.refresh(achievement)
    return achievement


@router.get(
    "/evaluations/latest",
    response_model=list[AchievementWorkflowSummary],
)
async def list_latest_evaluations(
    session: DatabaseSession,
    user: CurrentUser,
) -> list[AchievementWorkflowSummary]:
    filters = []
    if not user.is_superuser:
        filters.append(catalog_access_filter(user))
    result = await session.execute(
        select(AchievementEvaluation)
        .join(Achievement, Achievement.id == AchievementEvaluation.achievement_id)
        .where(*filters)
        .order_by(
            AchievementEvaluation.achievement_id,
            AchievementEvaluation.evaluation_version.desc(),
        )
    )
    latest: dict[str, AchievementEvaluation] = {}
    for item in result.scalars():
        latest.setdefault(item.achievement_id, item)
    return [
        AchievementWorkflowSummary(
            achievement_id=achievement_id,
            evaluation=AchievementEvaluationResponse.model_validate(item),
        )
        for achievement_id, item in latest.items()
    ]


@router.get("/{achievement_id}", response_model=AchievementResponse)
async def get_achievement(
    achievement_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> Achievement:
    achievement = await session.get(Achievement, achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="成果不存在")
    if (
        not user.is_superuser
        and not (achievement.organization_id is None and achievement.status == "approved")
        and not (
            user.organization_id is not None and achievement.organization_id == user.organization_id
        )
    ):
        raise HTTPException(status_code=403, detail="无权访问该成果")
    return achievement


@router.get(
    "/{achievement_id}/evaluations",
    response_model=list[AchievementEvaluationResponse],
)
async def list_achievement_evaluations(
    achievement_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> list[AchievementEvaluation]:
    await get_achievement(achievement_id, session, user)
    result = await session.execute(
        select(AchievementEvaluation)
        .where(AchievementEvaluation.achievement_id == achievement_id)
        .order_by(AchievementEvaluation.evaluation_version.desc())
    )
    return list(result.scalars())


@router.post(
    "/{achievement_id}/evaluations",
    response_model=AchievementEvaluationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_achievement_evaluation(
    achievement_id: str,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> AchievementEvaluation:
    achievement = await get_achievement(achievement_id, session, user)
    evaluation = await create_evaluation(session, achievement)
    if services.agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent Runtime 未初始化")
    run = await services.agent_runtime.create_run(
        session,
        user_id=user.id,
        organization_id=user.organization_id,
        message=f"请对科技成果《{achievement.title}》进行自身质量的六维独立评价。",
        task_type="evaluation",
        context={
            "source": "achievement_library",
            "domain_action": "achievement_evaluation",
            "achievement_id": achievement.id,
            "evaluation_id": evaluation.id,
        },
    )
    evaluation.agent_run_id = run.id
    await session.commit()
    await session.refresh(evaluation)
    dispatch_agent_run(background_tasks, services, run.id)
    return evaluation


@router.patch("/{achievement_id}", response_model=AchievementResponse)
async def update_achievement(
    achievement_id: str,
    payload: AchievementUpdate,
    session: DatabaseSession,
    user: CurrentUser,
) -> Achievement:
    achievement = await get_achievement(achievement_id, session, user)
    updates = payload.model_dump(exclude_unset=True)
    require_catalog_write(achievement, user)
    updated_title = updates.get("title")
    if updated_title:
        updates["normalized_title"] = normalize_title(updated_title)
    for key, value in updates.items():
        setattr(achievement, key, value)
    achievement.version += 1
    achievement.search_indexed = False
    session.add(
        OutboxEvent(
            aggregate_type="achievement",
            aggregate_id=achievement.id,
            event_type="achievement.reindex_requested",
            version=achievement.version,
            payload={"achievement_id": achievement.id, "version": achievement.version},
        )
    )
    await session.commit()
    await session.refresh(achievement)
    return achievement


@router.post("/{achievement_id}/decision", response_model=AchievementResponse)
async def decide_achievement(
    achievement_id: str,
    payload: AchievementDecision,
    session: DatabaseSession,
    reviewer: Reviewer,
) -> Achievement:
    achievement = await session.get(Achievement, achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="成果不存在")
    status_map = {"approve": "approved", "reject": "rejected", "archive": "archived"}
    require_catalog_write(achievement, reviewer)
    achievement.status = status_map[payload.decision]
    achievement.approved_by = reviewer.id if payload.decision == "approve" else None
    achievement.version += 1
    achievement.search_indexed = False
    session.add(
        OutboxEvent(
            aggregate_type="achievement",
            aggregate_id=achievement.id,
            event_type=(
                "achievement.approved"
                if payload.decision == "approve"
                else "achievement.removed_from_index"
            ),
            version=achievement.version,
            payload={
                "achievement_id": achievement.id,
                "version": achievement.version,
                "decision": payload.decision,
            },
        )
    )
    await session.commit()
    await session.refresh(achievement)
    return achievement
