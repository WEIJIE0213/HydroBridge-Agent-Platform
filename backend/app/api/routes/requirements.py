from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DatabaseSession, Services
from app.api.routes.workflow_helpers import dispatch_agent_run
from app.db.models.domain import (
    Achievement,
    Feedback,
    MatchItem,
    MatchRun,
    Requirement,
    RequirementRefinement,
)
from app.schemas.common import Page
from app.schemas.requirement import RequirementCreate, RequirementResponse, RequirementUpdate
from app.schemas.workflow import (
    MatchDecision,
    MatchItemResponse,
    MatchRunDetail,
    MatchRunSummary,
    RequirementRefinementResponse,
    RequirementWorkflowSummary,
)

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


@router.get(
    "/workflow-summaries",
    response_model=list[RequirementWorkflowSummary],
)
async def list_requirement_workflow_summaries(
    session: DatabaseSession,
    user: CurrentUser,
) -> list[RequirementWorkflowSummary]:
    requirements = list(
        (
            await session.execute(
                select(Requirement).where(
                    Requirement.created_by == user.id
                    if not user.is_superuser
                    else Requirement.id.is_not(None)
                )
            )
        ).scalars()
    )
    summaries: list[RequirementWorkflowSummary] = []
    for requirement in requirements:
        refinement = (
            await session.execute(
                select(RequirementRefinement)
                .where(RequirementRefinement.requirement_id == requirement.id)
                .order_by(RequirementRefinement.refinement_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        match = (
            await session.execute(
                select(MatchRun)
                .where(MatchRun.requirement_id == requirement.id)
                .order_by(MatchRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        top_score = None
        if match is not None:
            top_score = await session.scalar(
                select(func.max(MatchItem.final_score)).where(MatchItem.match_id == match.id)
            )
        summaries.append(
            RequirementWorkflowSummary(
                requirement_id=requirement.id,
                latest_refinement_status=refinement.status if refinement else None,
                latest_match_id=match.id if match else None,
                latest_match_status=match.status if match else None,
                latest_match_score=top_score,
                latest_match_candidate_count=match.candidate_count if match else 0,
                latest_match_at=match.created_at if match else None,
            )
        )
    return summaries


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


@router.post(
    "/{requirement_id}/refinements",
    response_model=RequirementRefinementResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refine_requirement(
    requirement_id: str,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> RequirementRefinement:
    requirement = await get_owned_requirement(requirement_id, session, user)
    version = (
        await session.scalar(
            select(func.count())
            .select_from(RequirementRefinement)
            .where(RequirementRefinement.requirement_id == requirement.id)
        )
        or 0
    ) + 1
    refinement = RequirementRefinement(
        requirement_id=requirement.id,
        refinement_version=version,
        status="queued",
    )
    session.add(refinement)
    await session.flush()
    if services.agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent Runtime 未初始化")
    run = await services.agent_runtime.create_run(
        session,
        user_id=user.id,
        organization_id=user.organization_id,
        message=requirement.raw_input,
        task_type="requirement_analysis",
        context={
            "source": "requirement_center",
            "domain_action": "requirement_refinement",
            "requirement_id": requirement.id,
            "refinement_id": refinement.id,
        },
    )
    refinement.agent_run_id = run.id
    requirement.status = "refining"
    await session.commit()
    await session.refresh(refinement)
    dispatch_agent_run(background_tasks, services, run.id)
    return refinement


@router.get(
    "/{requirement_id}/refinements",
    response_model=list[RequirementRefinementResponse],
)
async def list_requirement_refinements(
    requirement_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> list[RequirementRefinement]:
    await get_owned_requirement(requirement_id, session, user)
    result = await session.execute(
        select(RequirementRefinement)
        .where(RequirementRefinement.requirement_id == requirement_id)
        .order_by(RequirementRefinement.refinement_version.desc())
    )
    return list(result.scalars())


@router.post(
    "/{requirement_id}/matches",
    response_model=MatchRunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_requirement_match(
    requirement_id: str,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> MatchRunSummary:
    requirement = await get_owned_requirement(requirement_id, session, user)
    match = MatchRun(
        requirement_id=requirement.id,
        status="queued",
        rubric_snapshot={
            "dimensions": [
                "scenario_fit",
                "effectiveness",
                "maturity",
                "evidence_quality",
                "cost_fit",
                "implementation_risk",
            ],
            "hard_constraints": requirement.hard_constraints,
        },
    )
    session.add(match)
    await session.flush()
    if services.agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent Runtime 未初始化")
    message = requirement.normalized_description or requirement.raw_input
    run = await services.agent_runtime.create_run(
        session,
        user_id=user.id,
        organization_id=user.organization_id,
        message=message,
        task_type="matching",
        context={
            "source": "requirement_center",
            "domain_action": "requirement_matching",
            "requirement_id": requirement.id,
            "match_id": match.id,
        },
    )
    match.agent_run_id = run.id
    requirement.status = "matching"
    await session.commit()
    await session.refresh(match)
    dispatch_agent_run(background_tasks, services, run.id)
    return MatchRunSummary.model_validate({**match.__dict__, "top_score": None})


async def build_match_summary(session: DatabaseSession, match: MatchRun) -> MatchRunSummary:
    top_score = await session.scalar(
        select(func.max(MatchItem.final_score)).where(MatchItem.match_id == match.id)
    )
    return MatchRunSummary.model_validate({**match.__dict__, "top_score": top_score})


@router.get(
    "/{requirement_id}/matches",
    response_model=list[MatchRunSummary],
)
async def list_requirement_matches(
    requirement_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> list[MatchRunSummary]:
    await get_owned_requirement(requirement_id, session, user)
    matches = list(
        (
            await session.execute(
                select(MatchRun)
                .where(MatchRun.requirement_id == requirement_id)
                .order_by(MatchRun.created_at.desc())
            )
        ).scalars()
    )
    return [await build_match_summary(session, item) for item in matches]


@router.get(
    "/{requirement_id}/matches/{match_id}",
    response_model=MatchRunDetail,
)
async def get_requirement_match(
    requirement_id: str,
    match_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> MatchRunDetail:
    await get_owned_requirement(requirement_id, session, user)
    match = await session.get(MatchRun, match_id)
    if match is None or match.requirement_id != requirement_id:
        raise HTTPException(status_code=404, detail="匹配记录不存在")
    rows = await session.execute(
        select(MatchItem, Achievement.title)
        .join(Achievement, Achievement.id == MatchItem.achievement_id)
        .where(MatchItem.match_id == match.id)
        .order_by(MatchItem.rank)
    )
    items = [
        MatchItemResponse.model_validate({**item.__dict__, "achievement_title": achievement_title})
        for item, achievement_title in rows.all()
    ]
    summary = await build_match_summary(session, match)
    return MatchRunDetail.model_validate(
        {**summary.model_dump(), "rubric_snapshot": match.rubric_snapshot, "items": items}
    )


@router.post(
    "/{requirement_id}/matches/{match_id}/items/{item_id}/decision",
    response_model=MatchItemResponse,
)
async def decide_match_item(
    requirement_id: str,
    match_id: str,
    item_id: str,
    payload: MatchDecision,
    session: DatabaseSession,
    user: CurrentUser,
) -> MatchItemResponse:
    await get_owned_requirement(requirement_id, session, user)
    item = await session.get(MatchItem, item_id)
    if item is None or item.match_id != match_id:
        raise HTTPException(status_code=404, detail="候选成果不存在")
    achievement = await session.get(Achievement, item.achievement_id)
    if achievement is None:
        raise HTTPException(status_code=404, detail="成果不存在")
    item.decision_status = payload.decision
    item.decided_by = user.id
    item.decided_at = datetime.now(UTC)
    session.add(
        Feedback(
            user_id=user.id,
            target_type="match_item",
            target_id=item.id,
            accepted=(
                True
                if payload.decision == "adopted"
                else False
                if payload.decision == "rejected"
                else None
            ),
            comment=payload.comment,
            corrections={"decision": payload.decision},
        )
    )
    await session.commit()
    await session.refresh(item)
    return MatchItemResponse.model_validate(
        {**item.__dict__, "achievement_title": achievement.title}
    )
