from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.agents.model_gateway import ModelGateway
from app.agents.specialists import (
    AchievementEvaluationAgent,
    AgentDependencies,
    EvidenceCuratorAgent,
    FinalReportAgent,
    RequirementAnalystAgent,
    ResearchAgent,
    VerificationAgent,
    accepted_search_results,
)
from app.agents.state import AgentState
from app.core.config import settings
from app.core.exceptions import RunCancelledError
from app.db.session import AsyncSessionFactory
from app.services.container import ServiceContainer
from app.services.matching import EvidenceMatchingWorkflow
from app.services.research_ingestion import ResearchIngestionService


class DomainWorkflows:
    def __init__(self, services: ServiceContainer, model: ModelGateway | None = None) -> None:
        self.services = services
        self.model = model if model is not None else ModelGateway()
        self.requirement = RequirementAnalystAgent()
        self.research = ResearchAgent()
        self.curator = EvidenceCuratorAgent()
        self.research_ingestion = ResearchIngestionService()
        self.achievement_evaluation = AchievementEvaluationAgent()
        self.assessment = EvidenceMatchingWorkflow()
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

    async def requirement_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.requirement.name)
        async with AsyncSessionFactory() as session:
            profile = await self.requirement.analyze(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        next_step = (
            "human_review"
            if profile.requires_clarification
            else (
                "final_report_agent"
                if state.get("task_type") == "requirement_analysis"
                else "assessment_matching_agent"
            )
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
        clarification_payload = (
            self._requirement_clarification_payload(state, profile.model_dump())
            if profile.requires_clarification
            else {}
        )
        if profile.requires_clarification:
            await self._publish(
                state,
                "approval.required",
                self.requirement.name,
                {
                    "reason": "需求关键信息不足",
                    "payload": clarification_payload,
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
            "review_payload": clarification_payload,
            "next_step": next_step,
            "visited_agents": [*state.get("visited_agents", []), self.requirement.name],
        }

    @staticmethod
    def _requirement_clarification_payload(
        state: AgentState,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a UI-ready payload for requirement clarification.

        The graph still resumes with a single enriched user message, but the
        payload gives the frontend enough structure to render a business form
        instead of exposing raw JSON to the user.
        """

        field_templates = {
            "工程场景和问题描述": {
                "key": "scenario_and_problem",
                "label": "工程对象与核心问题",
                "reason": "工程对象和核心问题会直接决定候选成果范围。",
                "placeholder": "例如：北方农村供水泵站，人工巡检成本高且故障发现不及时",
                "suggestions": [
                    "北方农村供水泵站，冬季低温，人工巡检成本高且故障发现不及时",
                    "老旧城区供水管网漏损率较高，爆管定位慢，夜间巡检困难",
                    "大中型灌区渠系调度依赖人工经验，来水波动时配水不准确",
                ],
            },
            "工程对象": {
                "key": "engineering_object",
                "label": "具体工程对象",
                "reason": "泵站、管网、水厂、水库等对象需要匹配不同类型的成果。",
                "placeholder": "例如：农村供水泵站、输水管网或净水厂",
                "suggestions": [
                    "农村供水泵站及其机电设备",
                    "老旧城区输配水管网",
                    "大中型灌区干支渠和闸门群",
                ],
            },
            "核心问题": {
                "key": "core_problem",
                "label": "当前核心问题",
                "reason": "核心问题用于判断成果是否真正解决工程痛点。",
                "placeholder": "例如：人工巡检成本高、设备故障发现不及时",
                "suggestions": [
                    "设备依赖人工巡检，故障发现不及时，非计划停机风险较高",
                    "管网漏损定位慢，缺少分区计量和异常用水识别能力",
                    "调度依赖人工经验，来水变化时配水效率和准确性不足",
                ],
            },
            "现场约束": {
                "key": "site_constraints",
                "label": "施工与环境约束",
                "reason": "施工、气候和运行条件会影响成果是否可落地。",
                "placeholder": "例如：高寒环境，改造期间尽量不停水",
                "suggestions": [
                    "冬季最低温度较低，设备需适应高寒和室外潮湿环境",
                    "工程需要连续供水，改造期间不能长时间停水",
                    "现场通信条件一般，部分点位供电和网络不稳定",
                ],
            },
            "硬约束": {
                "key": "hard_constraints",
                "label": "不能违反的硬约束",
                "reason": "硬约束用于过滤不具备推荐资格的候选成果。",
                "placeholder": "例如：必须适用于低温环境、施工期间不能长时间停水",
                "suggestions": [
                    "必须适用于低温环境，并支持室外长期稳定运行",
                    "实施期间不得造成长时间停水，需支持分段改造",
                    "方案必须兼容现有设备和通信协议，不能整体更换系统",
                ],
            },
            "预算条件": {
                "key": "budget_constraints",
                "label": "预算与成本条件",
                "reason": "预算信息会影响成本适配度与实施方案。",
                "placeholder": "例如：预算有限，优先低维护成本方案",
                "suggestions": [
                    "预算有限，优先利用现有设备进行低成本改造",
                    "可以分阶段建设，首期优先覆盖高风险和关键点位",
                    "更关注全生命周期成本，优先选择低维护、易运维方案",
                ],
            },
        }

        missing_fields = list(profile.get("missing_critical_fields", []))
        fields: list[dict[str, Any]] = []
        for index, missing in enumerate(missing_fields):
            label = str(missing).strip() or f"待补充信息 {index + 1}"
            template = field_templates.get(label)
            if template is None:
                template = {
                    "key": f"additional_context_{index + 1}",
                    "label": label,
                    "reason": "该信息会影响候选成果的筛选和业务匹配结果。",
                    "placeholder": f"请补充{label}",
                    "suggestions": [],
                }
            fields.append(
                {
                    **template,
                    "input_type": "textarea",
                    "required": True,
                }
            )

        context = state.get("context", {})
        return {
            "type": "clarification",
            "title": "需求信息还不完整",
            "description": "补充以下关键信息后，系统会自动继续原来的任务。",
            "question": str(profile.get("clarification_question", "")),
            "completeness_score": float(profile.get("completeness_score", 0)),
            "missing_fields": missing_fields,
            "fields": fields,
            "current_profile": {
                "scenario": profile.get("scenario", ""),
                "problem": profile.get("problem", ""),
                "objectives": profile.get("objectives", []),
                "hard_constraints": profile.get("hard_constraints", {}),
                "soft_preferences": profile.get("soft_preferences", {}),
            },
            "original_message": state.get("user_message", ""),
            "requirement_id": context.get("requirement_id"),
            "match_id": context.get("match_id"),
            "resume_action": (
                "requirement_normalization"
                if state.get("task_type") == "requirement_analysis"
                else "requirement_matching"
            ),
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
            else "research_ingestion_agent"
            if state.get("task_type") == "research"
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

    async def research_ingestion_node(self, state: AgentState) -> dict[str, Any]:
        agent_name = "research_ingestion_agent"
        await self._publish(state, "agent.started", agent_name)
        if "ingestion_candidates" in state:
            candidates = list(state.get("ingestion_candidates", []))
        else:
            candidates = self.research_ingestion.score_candidates(
                state.get("evidence_records", []),
                state.get("search_results", []),
            )

        automatic = [item for item in candidates if item.get("route") == "auto_approved"]
        reviewable = [item for item in candidates if item.get("route") == "human_review"]
        previously_ingested = list(state.get("ingested_achievement_ids", []))
        async with AsyncSessionFactory() as session:
            auto_ids = await self.research_ingestion.persist_approved(
                session,
                automatic,
                organization_id=state.get("organization_id"),
                run_id=state["run_id"],
                approval_mode="automatic",
            )

        if reviewable and not state.get("ingestion_review_completed", False):
            review_items = [
                {
                    "candidate_id": item["candidate_id"],
                    "title": item.get("title", "未命名成果"),
                    "abstract": item.get("abstract", ""),
                    "organizations": item.get("organizations", []),
                    "confidence": item.get("ingestion_confidence", 0),
                    "score_components": item.get("score_components", {}),
                    "review_reasons": item.get("review_reasons", []),
                    "source_urls": item.get("source_urls", []),
                }
                for item in reviewable
            ]
            review_payload = {
                "type": "research_ingestion",
                "candidates": review_items,
                "thresholds": {
                    "review": self.research_ingestion.thresholds.review,
                    "auto_approve": self.research_ingestion.thresholds.auto_approve,
                },
                "summary": {
                    "auto_approved": len(automatic),
                    "awaiting_review": len(reviewable),
                    "rejected": sum(item.get("route") == "rejected" for item in candidates),
                    "deferred": sum(item.get("route") == "deferred" for item in candidates),
                },
            }
            await self._publish(
                state,
                "approval.required",
                agent_name,
                {
                    "reason": "发现置信度处于灰区的外部成果，请批量确认入库项",
                    "payload": review_payload,
                },
            )
            await self._publish(
                state,
                "agent.completed",
                agent_name,
                review_payload["summary"],
            )
            return {
                "ingestion_candidates": candidates,
                "ingested_achievement_ids": list(dict.fromkeys([*previously_ingested, *auto_ids])),
                "ingestion_summary": review_payload["summary"],
                "needs_human_review": True,
                "review_reason": "发现置信度处于灰区的外部成果，请批量确认入库项",
                "review_payload": review_payload,
                "next_step": "human_review",
                "visited_agents": [*state.get("visited_agents", []), agent_name],
            }

        approved_candidate_ids = set(state.get("ingestion_approved_candidate_ids", []))
        human_approved = [
            item for item in reviewable if item.get("candidate_id") in approved_candidate_ids
        ]
        async with AsyncSessionFactory() as session:
            reviewed_ids = await self.research_ingestion.persist_approved(
                session,
                human_approved,
                organization_id=state.get("organization_id"),
                run_id=state["run_id"],
                approval_mode="human_review",
                reviewer_id=state.get("ingestion_reviewer_id"),
            )
        summary = {
            "auto_approved": len(automatic),
            "human_approved": len(human_approved),
            "human_rejected": len(reviewable) - len(human_approved),
            "rejected": sum(item.get("route") == "rejected" for item in candidates),
            "deferred": sum(item.get("route") == "deferred" for item in candidates),
            "total_ingested": len(set([*auto_ids, *reviewed_ids])),
        }
        await self._publish(state, "agent.completed", agent_name, summary)
        return {
            "ingestion_candidates": candidates,
            "ingested_achievement_ids": list(
                dict.fromkeys([*previously_ingested, *auto_ids, *reviewed_ids])
            ),
            "ingestion_summary": summary,
            "needs_human_review": False,
            "next_step": "verification_agent",
            "visited_agents": [*state.get("visited_agents", []), agent_name],
        }

    async def assessment_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", "match_achievements")
        async with AsyncSessionFactory() as session:
            hits, batch = await self.assessment.match(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        next_step = "verification_agent"
        await self._publish(
            state,
            "agent.completed",
            "match_achievements",
            {"candidate_count": len(hits), "assessment_count": len(batch.results)},
        )
        context = state.get("context", {})
        explicit_scope = context.get("achievement_ids")
        if context.get("achievement_id"):
            explicit_scope = [context["achievement_id"]]
        if explicit_scope is None and (
            state.get("scope_is_set") or state.get("selected_achievement_ids")
        ):
            explicit_scope = state.get("selected_achievement_ids", [])
        return {
            "retrieval_hits": hits,
            "assessment_results": [item.model_dump() for item in batch.results],
            "assessment_generation_mode": batch.generation_mode,
            "selected_achievement_ids": explicit_scope
            if explicit_scope is not None
            else [item.achievement_id for item in batch.results],
            "scope_is_set": explicit_scope is not None or bool(batch.results),
            "next_step": next_step,
            "visited_agents": [*state.get("visited_agents", []), "match_achievements"],
        }

    async def achievement_evaluation_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.achievement_evaluation.name)
        async with AsyncSessionFactory() as session:
            evaluation = await self.achievement_evaluation.evaluate(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        await self._publish(
            state,
            "agent.completed",
            self.achievement_evaluation.name,
            {
                "overall_score": evaluation.overall_score,
                "confidence": evaluation.confidence,
            },
        )
        return {
            "achievement_evaluation": evaluation.model_dump(),
            "next_step": "verification_agent",
            "visited_agents": [
                *state.get("visited_agents", []),
                self.achievement_evaluation.name,
            ],
        }

    async def verifier_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", self.verifier.name)
        async with AsyncSessionFactory() as session:
            result = await self.verifier.verify(
                state,
                AgentDependencies(session, self.services, self.model),
            )
        research_task = state.get("task_type") == "research"
        if result.decision == "human_review_required" and not research_task:
            next_step = "human_review"
        elif (
            result.decision == "revision_required"
            and not research_task
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
        if result.decision == "human_review_required" and not research_task:
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
            "needs_human_review": result.decision == "human_review_required" and not research_task,
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
        decision_payload = decision if isinstance(decision, dict) else {"decision": str(decision)}
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
        review_type = state.get("review_payload", {}).get("type")
        decision_name = decision_payload.get("decision")
        if review_type == "research_ingestion":
            candidate_ids = {
                str(item.get("candidate_id"))
                for item in state.get("review_payload", {}).get("candidates", [])
                if item.get("candidate_id")
            }
            selected_ids: list[str] = []
            if decision_name in {"approve", "edit"}:
                requested_ids = (
                    edited_payload.get("approved_candidate_ids")
                    if isinstance(edited_payload, dict)
                    else None
                )
                if isinstance(requested_ids, list):
                    selected_ids = [
                        str(candidate_id)
                        for candidate_id in requested_ids
                        if str(candidate_id) in candidate_ids
                    ]
                elif decision_name == "approve":
                    selected_ids = sorted(candidate_ids)
            updates.update(
                {
                    "ingestion_review_completed": True,
                    "ingestion_approved_candidate_ids": selected_ids,
                    "ingestion_reviewer_id": decision_payload.get("reviewer_id"),
                }
            )
            next_step = "research_ingestion_agent"
        elif decision_name == "edit" and review_type == "clarification":
            next_step = "supervisor"
        elif decision_name == "approve" and review_type == "clarification":
            next_step = (
                "final_report_agent"
                if state.get("task_type") == "requirement_analysis"
                else "assessment_matching_agent"
            )
        else:
            next_step = "final_report_agent"
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
