from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DatabaseSession, require_roles
from app.db.models.domain import Achievement
from app.db.models.identity import User
from app.db.models.knowledge import OutboxEvent
from app.schemas.achievement import (
    AchievementCreate,
    AchievementDecision,
    AchievementResponse,
    AchievementUpdate,
)
from app.schemas.common import Page

router = APIRouter(prefix="/achievements", tags=["achievements"])
Reviewer = Annotated[User, Depends(require_roles("admin", "expert", "operator"))]


def normalize_title(title: str) -> str:
    return re.sub(r"[\s《》“”\"'（）()]+", "", title).casefold()


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
    if not user.is_superuser and user.organization_id:
        filters.append(
            or_(
                Achievement.organization_id == user.organization_id,
                Achievement.status == "approved",
            )
        )
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
        .where(*filters)
        .order_by(Achievement.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [AchievementResponse.model_validate(item) for item in result.scalars()]
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
        and achievement.status != "approved"
        and achievement.organization_id != user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权访问该成果")
    return achievement


@router.patch("/{achievement_id}", response_model=AchievementResponse)
async def update_achievement(
    achievement_id: str,
    payload: AchievementUpdate,
    session: DatabaseSession,
    user: CurrentUser,
) -> Achievement:
    achievement = await get_achievement(achievement_id, session, user)
    updates = payload.model_dump(exclude_unset=True)
    updated_title = updates.get("title")
    if updated_title:
        updates["normalized_title"] = normalize_title(updated_title)
    for key, value in updates.items():
        setattr(achievement, key, value)
    achievement.version += 1
    achievement.qdrant_indexed = False
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
    achievement.status = status_map[payload.decision]
    achievement.approved_by = reviewer.id if payload.decision == "approve" else None
    achievement.version += 1
    achievement.qdrant_indexed = False
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
