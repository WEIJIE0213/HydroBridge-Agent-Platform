from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpoint import checkpointer_context
from app.agents.graph import build_agent_graph
from app.core.config import settings
from app.core.exceptions import RunCancelledError
from app.core.logging import get_logger
from app.db.models.knowledge import Artifact
from app.db.models.runtime import (
    AgentRun,
    ApprovalTask,
    Conversation,
    Message,
)
from app.db.session import AsyncSessionFactory
from app.services.container import ServiceContainer

logger = get_logger("agent_runtime")


class AgentRuntime:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services

    async def create_run(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        organization_id: str | None,
        message: str,
        task_type: str,
        context: dict[str, Any],
        conversation_id: str | None = None,
    ) -> AgentRun:
        conversation: Conversation | None = None
        if conversation_id:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise ValueError("Conversation does not exist or is not owned by the user")
        if conversation is None:
            title = message.strip().replace("\n", " ")[:50] or "新任务"
            conversation = Conversation(user_id=user_id, title=title)
            session.add(conversation)
            await session.flush()

        sequence = (
            await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.conversation_id == conversation.id)
            )
            or 0
        ) + 1
        run = AgentRun(
            thread_id=conversation.id,
            run_sequence=sequence,
            conversation_id=conversation.id,
            user_id=user_id,
            task_type=task_type,
            status="queued",
            input_payload={
                "message": message,
                "context": context,
                "organization_id": organization_id,
            },
        )
        session.add(run)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                run_id=run.id,
                role="user",
                content=message,
            )
        )
        await session.commit()
        await session.refresh(run)
        await self.services.events.publish(
            run.id,
            "run.created",
            {"task_type": task_type, "conversation_id": conversation.id},
        )
        return run

    async def execute(self, run_id: str, resume_payload: dict[str, Any] | None = None) -> None:
        started = datetime.now(UTC)
        async with AsyncSessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"Agent run {run_id} does not exist")
            if run.status in {"completed", "cancelled"}:
                return
            run.status = "running"
            run.error_code = None
            run.error_message = ""
            await session.commit()
            input_payload = dict(run.input_payload)
            initial_state = {
                "run_id": run.id,
                "thread_id": run.thread_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "organization_id": input_payload.get("organization_id"),
                "task_type": run.task_type,
                "user_message": input_payload.get("message", ""),
                "context": input_payload.get("context", {}),
                "plan": [],
                "visited_agents": [],
                "iteration_count": 0,
                "errors": [],
            }

        await self.services.events.publish(
            run_id,
            "run.started" if resume_payload is None else "run.resuming",
            {"resumed": resume_payload is not None},
        )
        try:
            async with checkpointer_context() as checkpointer:
                graph = build_agent_graph(self.services, checkpointer=checkpointer)
                config = {
                    "configurable": {"thread_id": run.thread_id},
                    "recursion_limit": settings.agent_max_iterations * 8,
                }
                graph_input: Any = (
                    initial_state
                    if resume_payload is None
                    else Command(resume=resume_payload)
                )
                result = await asyncio.wait_for(
                    graph.ainvoke(graph_input, config=config),
                    timeout=settings.agent_run_timeout_seconds,
                )

            interrupts = result.get("__interrupt__", []) if isinstance(result, dict) else []
            async with AsyncSessionFactory() as session:
                run = await session.get(AgentRun, run_id)
                if run is None:
                    return
                if run.status == "cancelled":
                    return
                if interrupts:
                    run.status = "waiting_review"
                    run.current_agent = "human_review"
                    run.current_step = "waiting_review"
                    interrupt_value = getattr(interrupts[0], "value", {})
                    reason = (
                        interrupt_value.get("reason", "需要人工确认")
                        if isinstance(interrupt_value, dict)
                        else "需要人工确认"
                    )
                    existing = (
                        await session.execute(
                            select(ApprovalTask).where(
                                ApprovalTask.run_id == run_id,
                                ApprovalTask.status == "pending",
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(
                            ApprovalTask(
                                run_id=run_id,
                                requested_by_agent="human_review",
                                risk_level="medium",
                                reason=reason,
                                payload=(
                                    interrupt_value
                                    if isinstance(interrupt_value, dict)
                                    else {"value": str(interrupt_value)}
                                ),
                            )
                        )
                    await session.commit()
                    return

                final_answer = str(result.get("final_answer", ""))
                citations = list(result.get("citations", []))
                artifact_id: str | None = None
                report_bytes = final_answer.encode("utf-8")
                try:
                    object_key = f"runs/{run.id}/final-report.md"
                    await self.services.object_storage.put_bytes(
                        self.services.object_storage.artifact_bucket,
                        object_key,
                        report_bytes,
                        "text/markdown; charset=utf-8",
                    )
                    artifact_id = str(uuid.uuid4())
                    session.add(
                        Artifact(
                            id=artifact_id,
                            user_id=run.user_id,
                            agent_run_id=run.id,
                            artifact_type="agent_report",
                            title="HydroBridge Agent 分析报告",
                            mime_type="text/markdown; charset=utf-8",
                            object_key=object_key,
                            sha256=hashlib.sha256(report_bytes).hexdigest(),
                            metadata_json={
                                "task_type": run.task_type,
                                "citation_count": len(citations),
                                "encoding": "utf-8",
                            },
                        )
                    )
                except Exception:
                    logger.warning(
                        "report_artifact_persist_failed",
                        run_id=run.id,
                        exc_info=True,
                    )
                run.status = "completed"
                run.current_agent = None
                run.current_step = "completed"
                run.output_payload = {
                    "answer": final_answer,
                    "citations": citations,
                    "verification": result.get("verification", {}),
                    "assessments": result.get("assessment_results", []),
                    "requirement": result.get("requirement_profile", {}),
                    "artifact_id": artifact_id,
                }
                run.latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                session.add(
                    Message(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        role="assistant",
                        agent_name="supervisor",
                        content=final_answer,
                        citations=citations,
                    )
                )
                await session.commit()
            await self.services.events.publish(
                run_id,
                "run.completed",
                {"latency_ms": run.latency_ms, "artifact_id": artifact_id},
            )
            await self.services.redis.runtime.expire(
                self.services.events.stream_key(run_id),
                7 * 24 * 60 * 60,
            )
        except TimeoutError as exc:
            await self._fail_run(run_id, "RUN_TIMEOUT", "Agent 运行超过时间限制", exc)
        except RunCancelledError:
            await self._cancel_run(run_id)
        except Exception as exc:
            await self._fail_run(run_id, "RUN_FAILED", str(exc), exc)

    async def _cancel_run(self, run_id: str) -> None:
        async with AsyncSessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run is not None:
                run.status = "cancelled"
                run.current_agent = None
                run.current_step = "cancelled"
                await session.commit()
        await self.services.trace.fail_open_steps(run_id, "任务已由用户取消")
        await self.services.events.publish(run_id, "run.cancelled", {})

    async def _fail_run(
        self,
        run_id: str,
        error_code: str,
        message: str,
        exc: Exception,
    ) -> None:
        logger.exception(
            "agent_run_failed",
            run_id=run_id,
            error_code=error_code,
            error_type=type(exc).__name__,
        )
        async with AsyncSessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run:
                run.status = "failed"
                run.error_code = error_code
                run.error_message = message[:20_000]
                await session.commit()
        await self.services.trace.fail_open_steps(run_id, message)
        await self.services.events.publish(
            run_id,
            "run.failed",
            {"error_code": error_code, "message": message},
        )
