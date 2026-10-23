from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpoint import checkpointer_context
from app.agents.graph import AGENT_GRAPH_VERSIONS, GRAPH_VERSION, build_agent_graph
from app.agents.model_gateway import ModelGateway
from app.core.config import settings
from app.core.exceptions import RunCancelledError
from app.core.logging import get_logger
from app.db.models.domain import (
    AchievementEvaluation,
    MatchRun,
    Requirement,
    RequirementRefinement,
)
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
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
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
            graph_version=GRAPH_VERSION,
            thread_id=conversation.id,
            run_sequence=sequence,
            conversation_id=conversation.id,
            user_id=user_id,
            task_type=task_type,
            status="queued",
            input_payload={
                "message": message,
                "original_message": message,
                "requested_task_type": task_type,
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
        preparation_error = ""
        async with AsyncSessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"Agent run {run_id} does not exist")
            if run.status in {"completed", "cancelled"}:
                return
            run.status = "running"
            run.error_code = None
            run.error_message = ""
            await self.set_domain_status(session, run, "running")
            await session.commit()
            input_payload = dict(run.input_payload)
            graph_version = run.graph_version
            clarification_recovery_message = self._clarification_recovery_message(resume_payload)
            if clarification_recovery_message:
                input_payload["message"] = clarification_recovery_message
                run.input_payload = input_payload
                await session.commit()
            initial_state = {
                "run_id": run.id,
                "thread_id": run.thread_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "organization_id": input_payload.get("organization_id"),
                "task_type": run.task_type,
                "user_message": input_payload.get("message", ""),
                "original_request": input_payload.get(
                    "original_message", input_payload.get("message", "")
                ),
                "requested_task_type": input_payload.get("requested_task_type", run.task_type),
                "context": input_payload.get("context", {}),
                "plan": [],
                "visited_agents": [],
                "iteration_count": 0,
                "errors": [],
                "tool_results": [],
                "tool_iterations": 0,
                "pending_tool": {},
                "final_answer": "",
                "citations": [],
                "rag_result": {},
                "requirement_profile": {},
                "assessment_results": [],
                "verification": {},
                "selected_achievement_ids": [],
                "scope_is_set": False,
            }
            history = list(
                (
                    await session.scalars(
                        select(Message)
                        .join(AgentRun, Message.run_id == AgentRun.id)
                        .where(
                            Message.conversation_id == run.conversation_id,
                            Message.run_id != run.id,
                            AgentRun.run_sequence < run.run_sequence,
                            AgentRun.status == "completed",
                        )
                        .order_by(Message.created_at.desc())
                        .limit(8)
                    )
                ).all()
            )
            initial_state["conversation_history"] = [
                {"role": item.role, "content": item.content[:4000]} for item in reversed(history)
            ]
            previous = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == run.conversation_id,
                    AgentRun.status == "completed",
                    AgentRun.id != run.id,
                    AgentRun.run_sequence < run.run_sequence,
                )
                .order_by(AgentRun.run_sequence.desc())
                .limit(1)
            )
            if previous:
                prior_requirement_id = previous.output_payload.get("requirement_id")
                if prior_requirement_id and "requirement_id" not in initial_state["context"]:
                    initial_state["context"] = {
                        **initial_state["context"],
                        "requirement_id": prior_requirement_id,
                    }
                initial_state["selected_achievement_ids"] = previous.output_payload.get(
                    "selected_achievement_ids", []
                )
                initial_state["scope_is_set"] = previous.output_payload.get("scope_is_set", False)
                if "scope_limit" in previous.output_payload:
                    initial_state["scope_limit"] = previous.output_payload["scope_limit"]
                initial_state["active_candidates"] = previous.output_payload.get(
                    "active_candidates", []
                )
                initial_state["requirement_profile"] = previous.output_payload.get(
                    "requirement", {}
                )
                initial_state["requirement_description"] = previous.output_payload.get(
                    "requirement_description", ""
                )
            requirement_id = initial_state["context"].get("requirement_id")
            if requirement_id:
                requirement = await session.scalar(
                    select(Requirement).where(
                        Requirement.id == requirement_id,
                        Requirement.created_by == run.user_id,
                    )
                )
                if requirement is None:
                    preparation_error = "需求不存在或无权访问"
                else:
                    initial_state["requirement_description"] = requirement.raw_input
                    initial_state["requirement_profile"] = {
                        "title": requirement.title,
                        "normalized_description": requirement.normalized_description,
                        "scenario": requirement.scenario,
                        "problem": requirement.problem,
                        "objectives": requirement.objectives,
                        "hard_constraints": requirement.hard_constraints,
                        "soft_preferences": requirement.soft_preferences,
                        "known_conditions": requirement.known_conditions,
                        "search_terms": requirement.search_terms,
                        "missing_critical_fields": requirement.missing_fields,
                        "completeness_score": requirement.completeness_score,
                    }

        await self.services.events.publish(
            run_id,
            "run.started" if resume_payload is None else "run.resuming",
            {"resumed": resume_payload is not None},
        )
        try:
            if preparation_error:
                raise ValueError(preparation_error)
            async with aclosing(ModelGateway()) as model, checkpointer_context() as checkpointer:
                graph = build_agent_graph(
                    self.services, checkpointer=checkpointer, model=model, version=graph_version
                )
                config = {
                    "configurable": {"thread_id": run.id},
                    "recursion_limit": settings.agent_max_iterations * 24 + 32,
                }
                if resume_payload is not None and graph_version in AGENT_GRAPH_VERSIONS:
                    checkpoint = await graph.aget_state(config)
                    if not checkpoint.values or not checkpoint.next:
                        raise ValueError(
                            "恢复点不存在或已完成，请重新发起任务；已保存的业务结果仍然保留"
                        )
                graph_input: Any = (
                    initial_state if resume_payload is None else Command(resume=resume_payload)
                )
                try:
                    result = await asyncio.wait_for(
                        graph.ainvoke(graph_input, config=config),
                        timeout=settings.agent_run_timeout_seconds,
                    )
                except KeyError as error:
                    if not (
                        error.args == ("run_id",)
                        and graph_version not in AGENT_GRAPH_VERSIONS
                        and clarification_recovery_message
                        and resume_payload is not None
                    ):
                        raise
                    await self.services.events.publish(
                        run_id,
                        "run.checkpoint_recovered",
                        {"strategy": "restart_requirement_normalization"},
                    )
                    result = await asyncio.wait_for(
                        graph.ainvoke(initial_state, config=config),
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
                    await self.set_domain_status(session, run, "waiting_review")
                    await session.commit()
                    await self.services.events.publish(
                        run_id, "run.waiting_review", {"reason": reason}
                    )
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
                action_statuses = {
                    item.get("tool"): item.get("status") for item in result.get("tool_results", [])
                }
                run.output_payload = {
                    **run.output_payload,
                    "answer": final_answer,
                    "citations": citations,
                    "verification": result.get("verification", {}),
                    "assessments": result.get("assessment_results", []),
                    "achievement_evaluation": result.get("achievement_evaluation", {}),
                    "requirement": result.get("requirement_profile", {}),
                    "requirement_description": result.get("requirement_description", ""),
                    "requirement_id": result.get("context", {}).get("requirement_id"),
                    "refinement_id": result.get("context", {}).get("refinement_id"),
                    "match_id": result.get("context", {}).get("match_id"),
                    "evaluation_id": result.get("context", {}).get("evaluation_id"),
                    "research_ingestion": result.get("ingestion_summary", {}),
                    "ingested_achievement_ids": result.get("ingested_achievement_ids", []),
                    "artifact_id": artifact_id,
                    "selected_achievement_ids": result.get("selected_achievement_ids", []),
                    "scope_is_set": result.get("scope_is_set", False),
                    "scope_limit": result.get(
                        "scope_limit",
                        result.get("selected_achievement_ids", [])
                        if result.get("scope_is_set")
                        else None,
                    ),
                    "active_candidates": result.get("active_candidates", []),
                    "rag": result.get("rag_result", {}),
                    "tools": [item.get("tool") for item in result.get("tool_results", [])],
                    "actions": result.get("tool_results", []),
                    "memory": result.get("memory", {}),
                    "stop_reason": result.get("stop_reason", ""),
                    "clarification_question": result.get("agent_question", ""),
                    "task_completion": (
                        "partial"
                        if result.get("stop_reason")
                        or (
                            result.get("tool_results", [])
                            and result["tool_results"][-1].get("status") == "error"
                        )
                        else "needs_input"
                        if "needs_input" in action_statuses.values() or result.get("agent_question")
                        else "completed"
                    ),
                    "graph_version": graph_version,
                }
                if graph_version not in AGENT_GRAPH_VERSIONS:
                    await self._persist_domain_result(session, run, result)
                run.latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                session.add(
                    Message(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        role="assistant",
                        agent_name="hydrobridge_agent",
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

    @staticmethod
    def _clarification_recovery_message(
        resume_payload: dict[str, Any] | None,
    ) -> str:
        if not resume_payload or resume_payload.get("review_type") != "clarification":
            return ""
        if resume_payload.get("decision") != "edit":
            return ""
        edited_payload = resume_payload.get("edited_payload")
        if not isinstance(edited_payload, dict):
            return ""
        return str(edited_payload.get("message", "")).strip()

    async def _cancel_run(self, run_id: str) -> None:
        async with AsyncSessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run is not None:
                run.status = "cancelled"
                run.current_agent = None
                run.current_step = "cancelled"
                await self.set_domain_status(session, run, "cancelled")
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
                await self.set_domain_status(session, run, "failed")
                await session.commit()
        await self.services.trace.fail_open_steps(run_id, message)
        await self.services.events.publish(
            run_id,
            "run.failed",
            {"error_code": error_code, "message": message},
        )

    @staticmethod
    def _domain_context(run: AgentRun) -> dict[str, Any]:
        context = run.input_payload.get("context", {})
        return dict(context) if isinstance(context, dict) else {}

    async def set_domain_status(
        self,
        session: AsyncSession,
        run: AgentRun,
        status: str,
    ) -> None:
        context = self._domain_context(run)
        action = context.get("domain_action")
        target: AchievementEvaluation | RequirementRefinement | MatchRun | None = None
        if action == "achievement_evaluation" and context.get("evaluation_id"):
            target = await session.get(AchievementEvaluation, context["evaluation_id"])
        elif action == "requirement_refinement" and context.get("refinement_id"):
            target = await session.get(RequirementRefinement, context["refinement_id"])
        elif action == "requirement_matching" and context.get("match_id"):
            target = await session.get(MatchRun, context["match_id"])
        if target is not None and target.status != "completed":
            target.status = status

    async def _persist_domain_result(self, session, run, result):
        from app.services.workflow_results import WorkflowResultStore

        await WorkflowResultStore().persist(session, self._domain_context(run), result)
