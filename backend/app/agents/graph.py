from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.model_gateway import ModelGateway
from app.agents.specialists import (
    AgentDependencies,
    AssessmentMatchingAgent,
    EvidenceCuratorAgent,
    FinalReportAgent,
    RequirementAnalystAgent,
    ResearchAgent,
    SupervisorAgent,
    VerificationAgent,
    accepted_search_results,
)
from app.agents.state import AgentState
from app.core.config import settings
from app.core.exceptions import RunCancelledError
from app.db.session import AsyncSessionFactory
from app.services.container import ServiceContainer


class HydroBridgeGraph:
    def __init__(self, services: ServiceContainer) -> None:
        self.services = services
        self.model = ModelGateway()
        self.supervisor = SupervisorAgent()
        self.requirement = RequirementAnalystAgent()
        self.research = ResearchAgent()
        self.curator = EvidenceCuratorAgent()
        self.assessment = AssessmentMatchingAgent()
        self.verifier = VerificationAgent()
        self.report = FinalReportAgent()

    async def _publish(
        self,
        state: AgentState,
        event_type: str,
        agent: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if event_type == "agent.started":
            cancelled = await self.services.redis.runtime.get(
                f"hb:{settings.app_env}:run:{state['run_id']}:cancelled"
            )
            if cancelled:
                raise RunCancelledError("任务已由用户取消")
            await self.services.trace.agent_started(
                state["run_id"],
                agent,
                {
                    "task_type": state.get("task_type"),
                    "iteration": state.get("iteration_count", 0),
                },
            )
        elif event_type == "agent.completed":
            await self.services.trace.agent_completed(
                state["run_id"],
                agent,
                data,
            )
        await self.services.events.publish(
            state["run_id"],
            event_type,
            data or {},
            agent=agent,
            step=agent,
        )

    async def supervisor_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.supervisor.name)
        async with AsyncSessionFactory() as session:
            decision = await self.supervisor.decide(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        await self._publish(
            state,
            "plan.created",
            self.supervisor.name,
            decision.model_dump(),
        )
        await self._publish(
            state,
            "agent.completed",
            self.supervisor.name,
            {"task_type": decision.task_type, "plan": decision.plan},
        )
        return {
            "task_type": decision.task_type,
            "plan": decision.plan,
            "next_step": (
                "requirement_analyst"
                if decision.task_type in {"matching", "requirement_analysis"}
                else "research_agent"
                if decision.task_type == "research"
                else "assessment_matching_agent"
            ),
            "visited_agents": [*state.get("visited_agents", []), self.supervisor.name],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    async def requirement_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.requirement.name)
        async with AsyncSessionFactory() as session:
            profile = await self.requirement.analyze(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        next_step = "human_review" if profile.requires_clarification else (
            "final_report_agent"
            if state.get("task_type") == "requirement_analysis"
            else "assessment_matching_agent"
        )
        await self._publish(
            state,
            "agent.completed",
            self.requirement.name,
            {
                "completeness_score": profile.completeness_score,
                "requires_clarification": profile.requires_clarification,
            },
        )
        if profile.requires_clarification:
            await self._publish(
                state,
                "approval.required",
                self.requirement.name,
                {
                    "reason": "需求关键信息不足",
                    "payload": {
                        "type": "clarification",
                        "question": profile.clarification_question,
                        "missing_fields": profile.missing_critical_fields,
                    },
                },
            )
        return {
            "requirement_profile": profile.model_dump(),
            "clarification": {
                "question": profile.clarification_question,
                "missing_fields": profile.missing_critical_fields,
            },
            "needs_human_review": profile.requires_clarification,
            "review_reason": "需求关键信息不足" if profile.requires_clarification else "",
            "review_payload": (
                {
                    "type": "clarification",
                    "question": profile.clarification_question,
                    "missing_fields": profile.missing_critical_fields,
                }
                if profile.requires_clarification
                else {}
            ),
            "next_step": next_step,
            "visited_agents": [*state.get("visited_agents", []), self.requirement.name],
        }

    async def research_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.research.name)
        async with AsyncSessionFactory() as session:
            plan, results = await self.research.research(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        await self._publish(
            state,
            "agent.completed",
            self.research.name,
            {"queries": plan.queries, "result_count": len(results)},
        )
        return {
            "search_queries": plan.queries,
            "search_results": results,
            "next_step": "evidence_curator",
            "visited_agents": [*state.get("visited_agents", []), self.research.name],
        }

    async def curator_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.curator.name)
        async with AsyncSessionFactory() as session:
            batch = await self.curator.curate(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        next_step = (
            "assessment_matching_agent"
            if state.get("task_type") in {"matching", "evaluation"}
            else "verification_agent"
        )
        await self._publish(
            state,
            "agent.completed",
            self.curator.name,
            {"achievement_count": len(batch.achievements)},
        )
        return {
            "evidence_records": [item.model_dump() for item in batch.achievements],
            "rejected_sources": batch.rejected_sources,
            "next_step": next_step,
            "visited_agents": [*state.get("visited_agents", []), self.curator.name],
        }

    async def assessment_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.assessment.name)
        async with AsyncSessionFactory() as session:
            hits, batch = await self.assessment.match(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        if (
            state.get("task_type") in {"matching", "evaluation", "qa"}
            and not hits
            and self.services.search.configured
            and "research_agent" not in state.get("visited_agents", [])
        ):
            next_step = "research_agent"
        else:
            next_step = "verification_agent"
        await self._publish(
            state,
            "agent.completed",
            self.assessment.name,
            {"candidate_count": len(hits), "assessment_count": len(batch.results)},
        )
        return {
            "retrieval_hits": hits,
            "assessment_results": [item.model_dump() for item in batch.results],
            "next_step": next_step,
            "visited_agents": [*state.get("visited_agents", []), self.assessment.name],
        }

    async def verifier_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.verifier.name)
        async with AsyncSessionFactory() as session:
            result = await self.verifier.verify(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        if result.decision == "human_review_required":
            next_step = "human_review"
        elif (
            result.decision == "revision_required"
            and state.get("iteration_count", 0) < settings.agent_max_iterations
        ):
            next_step = "research_agent"
        else:
            next_step = "final_report_agent"
        await self._publish(
            state,
            "agent.completed",
            self.verifier.name,
            result.model_dump(),
        )
        if result.decision == "human_review_required":
            await self._publish(
                state,
                "approval.required",
                self.verifier.name,
                {
                    "reason": result.review_reason,
                    "payload": {
                        "type": "verification",
                        "verification": result.model_dump(),
                    },
                },
            )
        return {
            "verification": result.model_dump(),
            "needs_human_review": result.decision == "human_review_required",
            "review_reason": result.review_reason,
            "review_payload": {
                "type": "verification",
                "verification": result.model_dump(),
            },
            "next_step": next_step,
            "visited_agents": [*state.get("visited_agents", []), self.verifier.name],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    async def human_review_node(self, state: AgentState) -> dict[str, Any]:
        decision = interrupt(
            {
                "run_id": state["run_id"],
                "reason": state.get("review_reason", ""),
                "payload": state.get("review_payload", {}),
                "allowed_decisions": ["approve", "edit", "reject"],
            }
        )
        decision_payload = (
            decision if isinstance(decision, dict) else {"decision": str(decision)}
        )
        await self._publish(
            state,
            "run.resumed",
            "human_review",
            {"decision": decision_payload.get("decision", "")},
        )
        updates: dict[str, Any] = {}
        edited_payload = decision_payload.get("edited_payload")
        if isinstance(edited_payload, dict) and edited_payload.get("message"):
            updates["user_message"] = str(edited_payload["message"])
        next_step = (
            "supervisor"
            if decision_payload.get("decision") == "edit"
            and state.get("review_payload", {}).get("type") == "clarification"
            else "final_report_agent"
        )
        return {
            **updates,
            "human_decision": decision_payload,
            "needs_human_review": False,
            "next_step": next_step,
        }

    async def final_report_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.report.name)
        async with AsyncSessionFactory() as session:
            report = await self.report.render(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        citations = [
            {"title": item.get("title", ""), "url": item.get("url", "")}
            for item in accepted_search_results(state)
            if item.get("url")
        ][:20]
        await self._publish(
            state,
            "agent.completed",
            self.report.name,
            {"citation_count": len(citations)},
        )
        return {
            "final_answer": report,
            "citations": citations,
            "next_step": "end",
            "visited_agents": [*state.get("visited_agents", []), self.report.name],
        }

    @staticmethod
    def route(state: AgentState) -> str:
        return state.get("next_step", "final_report_agent")

    def build(self, checkpointer=None):
        graph = StateGraph(AgentState)
        graph.add_node("supervisor", self.supervisor_node)
        graph.add_node("requirement_analyst", self.requirement_node)
        graph.add_node("research_agent", self.research_node)
        graph.add_node("evidence_curator", self.curator_node)
        graph.add_node("assessment_matching_agent", self.assessment_node)
        graph.add_node("verification_agent", self.verifier_node)
        graph.add_node("human_review", self.human_review_node)
        graph.add_node("final_report_agent", self.final_report_node)
        graph.add_edge(START, "supervisor")

        route_map = {
            "supervisor": "supervisor",
            "requirement_analyst": "requirement_analyst",
            "research_agent": "research_agent",
            "evidence_curator": "evidence_curator",
            "assessment_matching_agent": "assessment_matching_agent",
            "verification_agent": "verification_agent",
            "human_review": "human_review",
            "final_report_agent": "final_report_agent",
            "end": END,
        }
        for node in (
            "supervisor",
            "requirement_analyst",
            "research_agent",
            "evidence_curator",
            "assessment_matching_agent",
            "verification_agent",
            "human_review",
            "final_report_agent",
        ):
            graph.add_conditional_edges(node, self.route, route_map)
        return graph.compile(checkpointer=checkpointer)


def build_agent_graph(services: ServiceContainer, checkpointer=None):
    return HydroBridgeGraph(services).build(checkpointer=checkpointer)
