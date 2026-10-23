"""The three business graphs. Each END returns control to the parent Agent loop."""

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents.rag_pipeline import RagPipeline
from app.agents.requirement_patch import RequirementChanges, describe_changes, merge_requirement
from app.agents.state import AgentState
from app.agents.tools import achievement_scope
from app.agents.workflows import DomainWorkflows
from app.core.config import settings
from app.core.exceptions import RunCancelledError
from app.db.models.domain import Achievement, Requirement
from app.db.session import AsyncSessionFactory
from app.services.chunking import chunk_achievement
from app.services.retrieval import visibility_filter


class BusinessSubgraphs(DomainWorkflows):
    async def analyze_requirement(self, state):
        patch = state.get("pending_tool", {}).get("arguments", {}).get("requirement_patch")
        if patch is not None:
            await self._publish(state, "agent.started", "requirement_patch")
            profile = merge_requirement(
                state.get("requirement_profile", {}), RequirementChanges.model_validate(patch)
            )
            description = profile["normalized_description"]
            await self._publish(state, "agent.completed", "requirement_patch")
            return {
                "requirement_profile": profile,
                "user_message": description,
                "requirement_description": description,
                "requirement_change_summary": describe_changes(patch),
                "next_step": "final_report_agent"
                if state["task_type"] == "requirement_analysis"
                else "assessment_matching_agent",
            }
        result = await self.requirement_node(state)
        return {**result, "requirement_description": state["user_message"]}

    async def review(self, state):
        result = await self.human_review_node(state)
        decision = result.get("human_decision", {}).get("decision")
        review_type = state.get("review_payload", {}).get("type")
        if decision == "reject" and review_type != "research_ingestion":
            raise RunCancelledError("用户拒绝继续本次业务任务")
        if result.get("next_step") == "supervisor":
            result["next_step"] = "requirement_analyst"
        return result

    async def update_requirement(self, state):
        await self._publish(state, "agent.started", "update_requirement")
        description = state["pending_tool"]["arguments"]["description"]
        requirement_id = state.get("context", {}).get("requirement_id")
        if requirement_id:
            async with AsyncSessionFactory() as session:
                requirement = await session.scalar(
                    select(Requirement)
                    .where(
                        Requirement.id == requirement_id,
                        Requirement.created_by == state["user_id"],
                    )
                    .with_for_update()
                )
                if requirement is None:
                    raise ValueError("需求不存在或无权修改")
                requirement.raw_input = description
                requirement.normalized_description = ""
                requirement.scenario = ""
                requirement.problem = ""
                requirement.objectives = []
                requirement.hard_constraints = {}
                requirement.soft_preferences = {}
                requirement.known_conditions = {}
                requirement.missing_fields = []
                requirement.search_terms = []
                requirement.completeness_score = 0
                requirement.status = "draft"
                await session.commit()
        await self._publish(state, "agent.completed", "update_requirement")
        return {
            "user_message": description,
            "requirement_description": description,
            "requirement_profile": {},
            "final_answer": "需求描述已更新，待重新分析或匹配。",
        }

    async def matching_verification(self, state):
        await self._publish(state, "agent.started", "constraint_validation")
        results = state.get("assessment_results", [])
        unknown = {
            check["constraint"]
            for item in results
            for check in item.get("constraint_checks", [])
            if check["status"] == "unknown"
        }
        verification = {
            "decision": "passed" if results else "rejected",
            "confidence": 0,
            "recommended_action": "按原文证据复核未知约束",
            "unknown_constraints": sorted(unknown),
        }
        await self._publish(state, "agent.completed", "constraint_validation", verification)
        return {"verification": verification}

    async def requirement_report(self, state):
        if state["task_type"] == "requirement_analysis":
            result = await self.final_report_node(state)
            if state.get("requirement_change_summary"):
                result["final_answer"] = (
                    state["requirement_change_summary"] + "\n\n" + result["final_answer"]
                )
            return result
        await self._publish(state, "agent.started", "matching_report")
        lines, citations = ["成果匹配结果（按检索相关度排序）", ""], []
        if state.get("requirement_change_summary"):
            lines.append(state["requirement_change_summary"])
        labels = {"satisfied": "满足", "violated": "不满足", "unknown": "未知"}
        for item in state.get("assessment_results", []):
            lines.append(
                f"{item['rank']}. {item['title']}：相关度 {item['final_score']:.1f}，"
                f"硬约束{labels[item['eligibility']]}"
            )
            for check in item.get("constraint_checks", []):
                basis = "原文证据核验" if check.get("method") == "evidence" else "规则核验"
                lines.append(
                    f"- {check['constraint']}：{labels[check['status']]}"
                    f"（{basis}）。{check['reason']}"
                )
                if check.get("quote") and check.get("method") == "evidence":
                    lines.extend(f"  > {line}" for line in check["quote"][:200].splitlines())
            for chunk in item.get("evidence", [])[:2]:
                lines.append(f"- 原文 [{len(citations) + 1}]：")
                lines.extend(f"  > {line}" for line in chunk["text"][:250].splitlines())
                citations.append(
                    {**chunk, "title": item["title"], "url": "", "quote": chunk["text"]}
                )
        if not state.get("assessment_results"):
            lines.append("当前范围内没有找到可核验的已审核成果。")
        lines.append("相关度仅用于排序；未知约束需要补充资料，不能视为满足。")
        lines.extend(state.get("retrieval_warnings", []))
        await self._publish(state, "agent.completed", "matching_report")
        return {"final_answer": "\n".join(lines), "citations": citations}

    async def detail(self, state):
        await self._publish(state, "agent.started", "get_achievement")
        achievement_id = state["pending_tool"]["arguments"]["achievement_id"]
        scope = achievement_scope(state)
        if scope is not None and achievement_id not in scope:
            return {
                "action_status": "needs_input",
                "final_answer": "成果超出当前限定范围，请重新选择。",
            }
        async with AsyncSessionFactory() as session:
            item = await session.scalar(
                select(Achievement).where(
                    Achievement.id == achievement_id,
                    Achievement.status == "approved",
                    visibility_filter(state.get("organization_id")),
                )
            )
            if item is None:
                return {
                    "action_status": "needs_input",
                    "final_answer": "成果不存在或不可访问，请重新选择有权限的成果。",
                }
            chunks = [
                chunk
                for chunk in chunk_achievement(item, settings.chunk_size, settings.chunk_overlap)
                if chunk.document_id is None
            ][:24]
            citations = [
                {
                    "title": item.title,
                    "url": "",
                    "chunk_id": chunk.id,
                    "achievement_id": item.id,
                    "version": item.version,
                    "field": chunk.field,
                    "quote": chunk.text,
                }
                for chunk in chunks
            ]
            answer = f"{item.title}\n\n" + "\n\n".join(
                f"{chunk.field}：{chunk.text}" for chunk in chunks
            )
        await self._publish(state, "agent.completed", "get_achievement")
        return {
            "final_answer": answer,
            "citations": citations,
            # Do not discard other candidates: a later action may compare them.
            "selected_achievement_ids": scope if scope is not None else [achievement_id],
            "scope_is_set": True,
        }

    def achievement_graph(self):
        graph = StateGraph(AgentState)
        for name, node in {
            "search_sources": self.research_node,
            "curate": self.curator_node,
            "ingest": self.research_ingestion_node,
            "evaluate": self.achievement_evaluation_node,
            "verify": self.verifier_node,
            "human_review": self.review,
            "report": self.final_report_node,
        }.items():
            graph.add_node(name, node)
        graph.add_conditional_edges(
            START,
            lambda s: "evaluate" if s["task_type"] == "evaluation" else "search_sources",
            {"evaluate": "evaluate", "search_sources": "search_sources"},
        )
        graph.add_edge("search_sources", "curate")
        graph.add_edge("curate", "ingest")
        graph.add_conditional_edges(
            "ingest",
            lambda s: "human_review" if s.get("needs_human_review") else "verify",
            {"human_review": "human_review", "verify": "verify"},
        )
        graph.add_edge("evaluate", "verify")
        graph.add_conditional_edges(
            "verify",
            lambda s: "human_review" if s.get("needs_human_review") else "report",
            {"human_review": "human_review", "report": "report"},
        )
        graph.add_conditional_edges(
            "human_review",
            lambda s: "ingest" if s.get("next_step") == "research_ingestion_agent" else "report",
            {"ingest": "ingest", "report": "report"},
        )
        graph.add_edge("report", END)
        return graph.compile()

    def requirement_graph(self):
        graph = StateGraph(AgentState)
        for name, node in {
            "update": self.update_requirement,
            "analyze": self.analyze_requirement,
            "human_review": self.review,
            "match": self.assessment_node,
            "verify": self.matching_verification,
            "report": self.requirement_report,
        }.items():
            graph.add_node(name, node)
        graph.add_conditional_edges(
            START,
            lambda s: "update" if s["pending_tool"]["name"] == "update_requirement" else "analyze",
            {"update": "update", "analyze": "analyze"},
        )
        graph.add_edge("update", END)
        graph.add_conditional_edges(
            "analyze",
            lambda s: s["next_step"],
            {
                "human_review": "human_review",
                "final_report_agent": "report",
                "assessment_matching_agent": "match",
            },
        )
        graph.add_conditional_edges(
            "human_review",
            lambda s: s["next_step"],
            {
                "requirement_analyst": "analyze",
                "final_report_agent": "report",
                "assessment_matching_agent": "match",
            },
        )
        graph.add_edge("match", "verify")
        graph.add_edge("verify", "report")
        graph.add_edge("report", END)
        return graph.compile()

    def qa_graph(self):
        rag = RagPipeline(self)
        graph = StateGraph(AgentState)
        for name, node in {
            "detail": self.detail,
            "prepare": rag.prepare,
            "retrieve": rag.retrieve,
            "generate": rag.generate,
            "validate": rag.validate,
            "render": rag.render,
        }.items():
            graph.add_node(name, node)
        graph.add_conditional_edges(
            START,
            lambda s: "detail" if s["pending_tool"]["name"] == "get_achievement" else "prepare",
            {"detail": "detail", "prepare": "prepare"},
        )
        graph.add_edge("detail", END)
        graph.add_conditional_edges(
            "prepare",
            lambda s: "done" if s.get("action_status") == "needs_input" else "retrieve",
            {"done": END, "retrieve": "retrieve"},
        )
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges(
            "validate", rag.next_step, {"retrieve": "retrieve", "render": "render"}
        )
        graph.add_edge("render", END)
        return graph.compile()
