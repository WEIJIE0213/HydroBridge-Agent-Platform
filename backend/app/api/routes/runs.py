from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DatabaseSession, Services
from app.core.config import settings
from app.db.models.runtime import (
    AgentRun,
    AgentStep,
    ApprovalTask,
    Conversation,
    ToolCall,
)
from app.db.session import AsyncSessionFactory
from app.schemas.common import Page, StatusResponse
from app.schemas.run import (
    AgentStepResponse,
    ApprovalResponse,
    ConversationResponse,
    RunCreate,
    RunDecision,
    RunResponse,
    RunTraceResponse,
    ToolCallResponse,
)

router = APIRouter(tags=["agent-runs"])


async def owned_run(
    run_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> AgentRun:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if run.user_id != user.id and not user.is_superuser:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return run


@router.post("/runs", response_model=RunResponse, status_code=202)
async def create_run(
    payload: RunCreate,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> AgentRun:
    if services.agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent Runtime 未初始化")
    if {"domain_action", "evaluation_id", "refinement_id", "match_id"} & payload.context.keys():
        raise HTTPException(status_code=400, detail="业务记录关联只能通过对应业务接口创建")
    from app.api.routes.achievements import get_achievement

    ids = payload.context.get("achievement_ids", [])
    if not isinstance(ids, list) or len(ids) > 20:
        raise HTTPException(status_code=422, detail="成果范围必须是最多20个ID的数组")
    if payload.context.get("achievement_id"):
        ids = [*ids, payload.context["achievement_id"]]
    for achievement_id in ids:
        await get_achievement(str(achievement_id), session, user)
    try:
        run = await services.agent_runtime.create_run(
            session,
            user_id=user.id,
            organization_id=user.organization_id,
            message=payload.message,
            task_type=payload.task_type,
            context=payload.context,
            conversation_id=payload.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if settings.agent_execution_mode == "inline":
        background_tasks.add_task(services.agent_runtime.execute, run.id)
    else:
        from app.worker.tasks import execute_agent_run

        execute_agent_run.delay(run.id)
    return run


@router.get("/runs", response_model=Page[RunResponse])
async def list_runs(
    session: DatabaseSession,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[RunResponse]:
    filters = [] if user.is_superuser else [AgentRun.user_id == user.id]
    total = await session.scalar(select(func.count()).select_from(AgentRun).where(*filters))
    result = await session.execute(
        select(AgentRun)
        .where(*filters)
        .order_by(AgentRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[RunResponse.model_validate(item) for item in result.scalars()],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> AgentRun:
    return await owned_run(run_id, session, user)


@router.get("/runs/{run_id}/trace", response_model=RunTraceResponse)
async def get_run_trace(
    run_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> RunTraceResponse:
    run = await owned_run(run_id, session, user)
    steps = (
        await session.execute(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)
        )
    ).scalars()
    tools = (
        await session.execute(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at)
        )
    ).scalars()
    return RunTraceResponse(
        run=RunResponse.model_validate(run),
        steps=[AgentStepResponse.model_validate(item) for item in steps],
        tool_calls=[ToolCallResponse.model_validate(item) for item in tools],
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
    last_event_id_query: str | None = Query(default=None, alias="last_event_id"),
) -> StreamingResponse:
    await owned_run(run_id, session, user)
    cursor = last_event_id_header or last_event_id_query or "0-0"

    async def is_terminal() -> bool:
        async with AsyncSessionFactory() as fresh_session:
            current = await fresh_session.get(AgentRun, run_id)
            return bool(current and current.status in {"completed", "failed", "cancelled"})

    return StreamingResponse(
        services.events.stream(run_id, cursor, is_terminal),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", response_model=StatusResponse)
async def cancel_run(
    run_id: str,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> StatusResponse:
    run = await owned_run(run_id, session, user)
    if run.status in {"completed", "failed", "cancelled"}:
        return StatusResponse(status=run.status, message="任务已经结束")
    run.status = "cancelled"
    await session.commit()
    await services.redis.runtime.set(
        f"hb:{settings.app_env}:run:{run_id}:cancelled",
        "1",
        ex=24 * 60 * 60,
    )
    await services.events.publish(run_id, "run.cancelled", {"by": user.id})
    return StatusResponse(status="cancelled", message="任务已取消")


@router.post("/runs/{run_id}/decisions", response_model=StatusResponse)
async def decide_run(
    run_id: str,
    payload: RunDecision,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> StatusResponse:
    run = await owned_run(run_id, session, user)
    if services.agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent Runtime 未初始化")
    approval = await session.get(ApprovalTask, payload.approval_id)
    if approval is None or approval.run_id != run.id:
        raise HTTPException(status_code=404, detail="审核任务不存在")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="审核任务已经处理")
    approval.status = "completed"
    approval.decision = payload.decision
    approval.decision_comment = payload.comment
    approval.decided_by = user.id
    run.status = "queued"
    await services.agent_runtime.set_domain_status(session, run, "queued")
    await session.commit()

    approval_payload = approval.payload if isinstance(approval.payload, dict) else {}
    workflow_payload = approval_payload.get("payload", {})
    resume_payload = {
        "decision": payload.decision,
        "comment": payload.comment,
        "edited_payload": payload.edited_payload,
        "reviewer_id": user.id,
        "review_type": (
            workflow_payload.get("type") if isinstance(workflow_payload, dict) else None
        ),
    }
    if settings.agent_execution_mode == "inline":
        background_tasks.add_task(services.agent_runtime.execute, run.id, resume_payload)
    else:
        from app.worker.tasks import resume_agent_run

        resume_agent_run.delay(run.id, resume_payload)
    return StatusResponse(status="queued", message="审核结果已提交，任务将继续执行")


@router.get("/approvals", response_model=Page[ApprovalResponse])
async def list_approvals(
    session: DatabaseSession,
    user: CurrentUser,
    status_filter: str = Query(default="pending", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[ApprovalResponse]:
    filters = [ApprovalTask.status == status_filter]
    if not user.is_superuser:
        filters.append(
            or_(
                ApprovalTask.assigned_to.is_(None),
                ApprovalTask.assigned_to == user.id,
            )
        )
    total = await session.scalar(select(func.count()).select_from(ApprovalTask).where(*filters))
    result = await session.execute(
        select(ApprovalTask)
        .where(*filters)
        .order_by(ApprovalTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[ApprovalResponse.model_validate(item) for item in result.scalars()],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/conversations", response_model=Page[ConversationResponse])
async def list_conversations(
    session: DatabaseSession,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[ConversationResponse]:
    filters = [Conversation.user_id == user.id]
    total = await session.scalar(select(func.count()).select_from(Conversation).where(*filters))
    result = await session.execute(
        select(Conversation)
        .where(*filters)
        .order_by(Conversation.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[ConversationResponse.model_validate(item) for item in result.scalars()],
        total=total or 0,
        page=page,
        page_size=page_size,
    )
