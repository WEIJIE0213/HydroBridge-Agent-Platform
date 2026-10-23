"""Atomic result journal and business persistence for compound Agent requests."""

from sqlalchemy import select

from app.core.exceptions import RunCancelledError
from app.db.models.runtime import AgentRun
from app.db.session import AsyncSessionFactory
from app.services.workflow_results import WorkflowResultStore


async def persist_action(state, entry):
    async with AsyncSessionFactory() as session:
        run = await session.scalar(
            select(AgentRun).where(AgentRun.id == state["run_id"]).with_for_update()
        )
        if run is None:
            return
        if run.user_id != state["user_id"]:
            raise ValueError("无权保存当前运行的结果")
        if run.status == "cancelled":
            raise RunCancelledError("任务已取消")
        entries = list(run.output_payload.get("actions", []))
        if any(item["action_id"] == entry["action_id"] for item in entries):
            return
        if entry["status"] == "completed":
            await WorkflowResultStore().persist(session, state.get("context", {}), state)
        run.output_payload = {
            **run.output_payload,
            "actions": [*entries, entry],
            "memory": state.get("memory", {}),
        }
        await session.commit()


async def completed_action(run_id, action_id):
    async with AsyncSessionFactory() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            return None
        return next(
            (
                item
                for item in run.output_payload.get("actions", [])
                if item["action_id"] == action_id
            ),
            None,
        )
