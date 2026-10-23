from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.db.models.runtime import AgentRun, AgentStep, ToolCall
from app.db.session import AsyncSessionFactory


class RunTraceService:
    """Persists the durable execution trace shown in the run inspector."""

    async def agent_started(
        self,
        run_id: str,
        agent_name: str,
        input_summary: dict[str, Any] | None = None,
    ) -> str:
        async with AsyncSessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return ""
            sequence = (
                await session.scalar(
                    select(func.max(AgentStep.sequence)).where(AgentStep.run_id == run_id)
                )
                or 0
            ) + 1
            step = AgentStep(
                run_id=run_id,
                sequence=sequence,
                agent_name=agent_name,
                node_name=agent_name,
                status="running",
                input_summary=input_summary or {},
                started_at=datetime.now(UTC).isoformat(),
            )
            session.add(step)
            run.current_agent = agent_name
            run.current_step = agent_name
            await session.commit()
            return step.id

    async def agent_completed(
        self,
        run_id: str,
        agent_name: str,
        output_summary: dict[str, Any] | None = None,
    ) -> None:
        async with AsyncSessionFactory() as session:
            step = (
                await session.execute(
                    select(AgentStep)
                    .where(
                        AgentStep.run_id == run_id,
                        AgentStep.agent_name == agent_name,
                        AgentStep.status == "running",
                    )
                    .order_by(AgentStep.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if step is not None:
                finished = datetime.now(UTC)
                step.status = "completed"
                step.finished_at = finished.isoformat()
                step.output_summary = output_summary or {}
                try:
                    started = datetime.fromisoformat(step.started_at)
                    step.latency_ms = max(
                        0,
                        int((finished - started).total_seconds() * 1000),
                    )
                except ValueError:
                    step.latency_ms = 0
            await session.commit()

    async def tool_started(
        self,
        run_id: str,
        tool_name: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
    ) -> tuple[str, float]:
        async with AsyncSessionFactory() as session:
            existing = await session.scalar(
                select(ToolCall).where(ToolCall.idempotency_key == idempotency_key)
            )
            if existing is not None:
                existing.status = "running"
                existing.retry_count += 1
                existing.input_payload = input_payload
                await session.commit()
                return existing.id, time.perf_counter()

            step_id = await session.scalar(
                select(AgentStep.id)
                .where(
                    AgentStep.run_id == run_id,
                    AgentStep.status == "running",
                )
                .order_by(AgentStep.sequence.desc())
                .limit(1)
            )
            call = ToolCall(
                run_id=run_id,
                step_id=step_id,
                tool_name=tool_name,
                idempotency_key=idempotency_key,
                status="running",
                input_payload=input_payload,
            )
            session.add(call)
            await session.commit()
            return call.id, time.perf_counter()

    async def tool_completed(
        self,
        tool_call_id: str,
        started_at: float,
        output_summary: dict[str, Any],
    ) -> None:
        async with AsyncSessionFactory() as session:
            call = await session.get(ToolCall, tool_call_id)
            if call is None:
                return
            call.status = "completed"
            call.output_summary = output_summary
            call.latency_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            call.error_code = None
            call.error_message = ""
            await session.commit()

    async def tool_failed(
        self,
        tool_call_id: str,
        started_at: float,
        error: Exception,
    ) -> None:
        async with AsyncSessionFactory() as session:
            call = await session.get(ToolCall, tool_call_id)
            if call is None:
                return
            call.status = "failed"
            call.latency_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            call.error_code = type(error).__name__
            call.error_message = str(error)[:20_000]
            await session.commit()

    async def fail_open_steps(self, run_id: str, message: str) -> None:
        async with AsyncSessionFactory() as session:
            steps = (
                await session.execute(
                    select(AgentStep).where(
                        AgentStep.run_id == run_id,
                        AgentStep.status == "running",
                    )
                )
            ).scalars()
            finished_at = datetime.now(UTC).isoformat()
            for step in steps:
                step.status = "failed"
                step.finished_at = finished_at
                step.error_message = message[:20_000]
            calls = await session.scalars(
                select(ToolCall).where(
                    ToolCall.run_id == run_id,
                    ToolCall.status == "running",
                )
            )
            for call in calls:
                call.status = "failed"
                call.error_code = "RUN_INTERRUPTED"
                call.error_message = message[:20_000]
            await session.commit()
