from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DatabaseSession
from app.db.models.domain import Requirement
from app.schemas.common import Page
from app.schemas.requirement import RequirementCreate, RequirementResponse, RequirementUpdate

router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.get("", response_model=Page[RequirementResponse])
async def list_requirements(
    session: DatabaseSession,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[RequirementResponse]:
    filters = [Requirement.created_by == user.id]
    count = await session.scalar(select(func.count()).select_from(Requirement).where(*filters))
    result = await session.execute(
        select(Requirement)
        .where(*filters)
        .order_by(Requirement.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[RequirementResponse.model_validate(item) for item in result.scalars()],
        total=count or 0,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=RequirementResponse, status_code=status.HTTP_201_CREATED)
async def create_requirement(
    payload: RequirementCreate,
    session: DatabaseSession,
    user: CurrentUser,
) -> Requirement:
    requirement = Requirement(
        organization_id=user.organization_id,
        created_by=user.id,
        title=payload.title,
        raw_input=payload.raw_input,
    )
    session.add(requirement)
    await session.commit()
    await session.refresh(requirement)
    return requirement


async def get_owned_requirement(
    requirement_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> Requirement:
    requirement = await session.get(Requirement, requirement_id)
    if requirement is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    if requirement.created_by != user.id and not user.is_superuser:
        raise HTTPException(status_code=403, detail="无权访问该需求")
    return requirement


@router.get("/{requirement_id}", response_model=RequirementResponse)
async def get_requirement(
    requirement_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> Requirement:
    return await get_owned_requirement(requirement_id, session, user)


@router.patch("/{requirement_id}", response_model=RequirementResponse)
async def update_requirement(
    requirement_id: str,
    payload: RequirementUpdate,
    session: DatabaseSession,
    user: CurrentUser,
) -> Requirement:
    requirement = await get_owned_requirement(requirement_id, session, user)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(requirement, key, value)
    await session.commit()
    await session.refresh(requirement)
    return requirement
