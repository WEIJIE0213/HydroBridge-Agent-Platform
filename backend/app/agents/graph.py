from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select

from app.agents.specialists import AgentDependencies
from app.agents.state import AgentState
from app.agents.workflows import DomainWorkflows
from app.core.config import settings
from app.db.models.domain import Achievement, Requirement
from app.db.models.identity import User
from app.db.session import AsyncSessionFactory
from app.services.chunking import chunk_achievement
from app.services.rag import AgenticRag
from app.services.retrieval import visibility_filter
from app.services.workflow_records import bind_workflow


class SearchArguments(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    achievement_ids: list[str] | None = Field(default=None, max_length=20)


class MatchArguments(BaseModel):
    query: str = Field(min_length=1, max_length=10000)


class DetailArguments(BaseModel):
    achievement_id: str


class CompareArguments(BaseModel):
    achievement_ids: list[str] = Field(min_length=2, max_length=5)
    question: str = Field(default="比较适用场景、技术指标与限制条件", max_length=5000)


class UpdateArguments(BaseModel):
    description: str = Field(min_length=1, max_length=10000)


class EvaluateArguments(BaseModel):
    achievement_id: str | None = None
    title: str | None = Field(default=None, max_length=300)


TOOL_SCHEMAS = {
    "research_achievements": (MatchArguments, "联网搜寻新成果、提取证据并按规则入库或转人工审核"),
    "evaluate_achievement": (
        EvaluateArguments,
        "评价一项成果自身质量；提供已知成果ID或准确名称，未指定时使用当前唯一成果",
    ),
    "normalize_requirement": (MatchArguments, "只整理和澄清工程需求，本次不执行成果匹配"),
    "search_achievements": (SearchArguments, "搜索成果并根据原文回答问题；最多两轮检索"),
    "match_achievements": (
        MatchArguments,
        "固定流程：需求解析、过滤、混合召回、成果聚合、重排、三态核验",
    ),
    "get_achievement": (DetailArguments, "读取已授权成果的结构化概览；不包含完整原件正文"),
    "compare_achievements": (CompareArguments, "在给定成果范围内依据原文对比"),
    "update_requirement": (UpdateArguments, "更新当前会话的需求；已有需求记录时保存新描述"),
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema.model_json_schema(),
        },
    }
    for name, (schema, description) in TOOL_SCHEMAS.items()
]


class HydroBridgeGraph(DomainWorkflows):
    """One decision-making agent; domain workflows have fixed server-side steps."""

    async def agent_node(self, state: AgentState) -> dict[str, Any]:
        await self._publish(state, "agent.started", "hydrobridge_agent")
        iterations = state.get("tool_iterations", 0)
        task = state.get("task_type", "auto")
        if not iterations and task in {
            "research", "evaluation", "requirement_analysis", "matching"
        }:
            updates = await self._start_workflow(state, task, state["user_message"])
            await self._publish(state, "agent.completed", "hydrobridge_agent", {"workflow": task})
            return updates
        if iterations >= min(settings.agent_max_iterations, 4):
            call = {}
        elif self.model.configured:
            async with AsyncSessionFactory() as session:
                call = await self.model.choose_tool(
                    TOOLS,
                    {
                        "request": state["user_message"],
                        "task": task,
                        "history": state.get("conversation_history", []),
                        "scope": self._scope(state),
                        "requirement": state.get("requirement_profile", {}),
                        "results": state.get("tool_results", []),
                    },
                    session=session,
                    run_id=state["run_id"],
                )
        else:
            call = {} if iterations else self._offline_tool(state)
        # A grounded tool result is mandatory for catalog answers.
        if not call and not iterations:
            call = self._offline_tool(state)
        if call and call.get("name") not in TOOL_SCHEMAS:
            raise ValueError("Agent requested an unknown tool")
        await self._publish(
            state,
            "agent.completed",
            "hydrobridge_agent",
            {"tool": call.get("name"), "iteration": iterations},
        )
        return {"pending_tool": call, "agent_route": "tools" if call else "respond"}

    @staticmethod
    def _scope(state):
        context = state.get("context", {})
        if "achievement_ids" in context:
            return list(context["achievement_ids"])
        if context.get("achievement_id"):
            return [str(context["achievement_id"])]
        if state.get("scope_is_set"):
            return state.get("selected_achievement_ids", [])
        return state.get("selected_achievement_ids") or None

    @staticmethod
    def _offline_tool(state):
        message = state["user_message"]
        scope = HydroBridgeGraph._scope(state)
        if any(w in message for w in ("联网", "上网", "调研", "搜寻")):
            return {"name": "research_achievements", "arguments": {"query": message}}
        if "规范化" in message or ("整理" in message and "需求" in message):
            return {"name": "normalize_requirement", "arguments": {"query": message}}
        if any(w in message for w in ("评价", "评估")):
            return {
                "name": "evaluate_achievement",
                "arguments": {"achievement_id": scope[0]} if scope and len(scope) == 1 else {},
            }
        if state.get("task_type") == "matching" or any(w in message for w in ("匹配", "推荐方案")):
            return {"name": "match_achievements", "arguments": {"query": message}}
        if scope and len(scope) >= 2 and any(w in message for w in ("对比", "比较")):
            return {
                "name": "compare_achievements",
                "arguments": {"achievement_ids": scope[:5], "question": message},
            }
        return {
            "name": "search_achievements",
            "arguments": {"query": message, "achievement_ids": scope},
        }

    async def _start_workflow(self, state, task, message, evaluation_args=None):
        context = dict(state.get("context", {}))
        updates = {}
        if task == "evaluation":
            args = evaluation_args or EvaluateArguments(
                achievement_id=context.get("achievement_id")
            )
            scope = self._scope(state)
            achievement_id = args.achievement_id
            if not achievement_id and not args.title and scope and len(scope) == 1:
                achievement_id = scope[0]
            async with AsyncSessionFactory() as session:
                user = await session.get(User, state["user_id"])
                organization_id = state.get("organization_id")
                access = and_(
                    Achievement.organization_id.is_(None), Achievement.status == "approved"
                )
                if organization_id:
                    access = or_(access, Achievement.organization_id == organization_id)
                statement = select(Achievement)
                if not (user and user.is_superuser):
                    statement = statement.where(access)
                if scope is not None:
                    statement = statement.where(Achievement.id.in_(scope))
                if achievement_id:
                    statement = statement.where(Achievement.id == achievement_id)
                elif args.title:
                    statement = statement.where(Achievement.title == args.title.strip())
                else:
                    statement = statement.where(Achievement.title == message.strip())
                candidates = list((await session.scalars(statement.limit(2))).all())
            if len(candidates) != 1:
                return {
                    "agent_route": "respond",
                    "final_answer": "请提供要评价的成果准确名称，或先检索并选定一项成果后再评价。",
                }
            achievement_id = candidates[0].id
            context["achievement_id"] = achievement_id
            updates = {"selected_achievement_ids": [achievement_id], "scope_is_set": True}
        context = await bind_workflow(state, task, message, context)
        return {
            **updates,
            "context": context,
            "task_type": task,
            "user_message": message,
            "workflow_step": {
                "research": "research_agent",
                "evaluation": "achievement_evaluation_agent",
                "requirement_analysis": "requirement_analyst",
                "matching": "requirement_analyst",
            }[task],
            "agent_route": "workflow",
        }

    async def tools_node(self, state: AgentState) -> dict[str, Any]:
        call = state["pending_tool"]
        name = call["name"]
        args = TOOL_SCHEMAS[name][0].model_validate(call.get("arguments", {}))
        iteration = state.get("tool_iterations", 0) + 1
        await self._publish(state, "agent.started", "tools", {"tool": name})
        await self._publish(state, "tool.started", "tools", {"tool": name})
        trace = None
        if hasattr(self.services.trace, "tool_started"):
            trace = await self.services.trace.tool_started(
                state["run_id"], name, f"{state['run_id']}:{iteration}:{name}", args.model_dump()
            )
        try:
            updates: dict[str, Any] = {}
            workflow_tasks = {
                "research_achievements": "research",
                "evaluate_achievement": "evaluation",
                "normalize_requirement": "requirement_analysis",
                "match_achievements": "matching",
            }
            if name in workflow_tasks:
                updates = await self._start_workflow(
                    state,
                    workflow_tasks[name],
                    state["user_message"] if name == "evaluate_achievement" else args.query,
                    args if name == "evaluate_achievement" else None,
                )
                result = {"tool": name, "status": updates["agent_route"]}
            else:
                async with AsyncSessionFactory() as session:
                    deps = AgentDependencies(session, self.services, self.model)
                    scope = self._scope(state)
                    if name in {"search_achievements", "compare_achievements"}:
                        requested = args.achievement_ids
                        allowed = (
                            scope
                            if requested is None
                            else (
                                [item for item in requested if item in scope]
                                if scope is not None
                                else requested
                            )
                        )
                        result = await AgenticRag().answer(
                            state,
                            deps,
                            query=args.query if name == "search_achievements" else args.question,
                            achievement_ids=allowed,
                        )
                        updates = {
                            "rag_result": result,
                            "citations": result["citations"],
                            "selected_achievement_ids": allowed
                            if allowed is not None
                            else result["achievement_ids"],
                            "scope_is_set": allowed is not None or bool(result["achievement_ids"]),
                            "agent_route": "respond",
                        }
                    elif name == "get_achievement":
                        if scope is not None and args.achievement_id not in scope:
                            raise ValueError("成果超出当前授权范围")
                        item = await session.scalar(
                            select(Achievement).where(
                                Achievement.id == args.achievement_id,
                                Achievement.status == "approved",
                                visibility_filter(state.get("organization_id")),
                            )
                        )
                        if item is None:
                            raise ValueError("成果不存在或不可访问")
                        chunks = [
                            c
                            for c in chunk_achievement(
                                item, settings.chunk_size, settings.chunk_overlap
                            )
                            if c.document_id is None
                        ][:24]
                        citations = [
                            {
                                "title": item.title,
                                "url": "",
                                "chunk_id": c.id,
                                "achievement_id": item.id,
                                "version": item.version,
                                "field": c.field,
                                "quote": c.text,
                            }
                            for c in chunks
                        ]
                        result = {
                            "answer": f"{item.title}\n\n"
                            + "\n\n".join(f"{c.field}：{c.text}" for c in chunks),
                            "citations": citations,
                            "source_document_count": len(
                                (item.structured_data or {}).get("source_documents", [])
                            ),
                            "detail_scope": (
                                "结构化概览；完整原文请通过 search_achievements 工具定向检索"
                            ),
                        }
                        updates = {"selected_achievement_ids": [item.id], "citations": citations}
                        updates["scope_is_set"] = True
                    else:
                        requirement_id = state.get("context", {}).get("requirement_id")
                        if requirement_id:
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
                            requirement.raw_input = args.description
                            requirement.normalized_description = ""
                            requirement.scenario = ""
                            requirement.problem = ""
                            requirement.objectives = []
                            requirement.hard_constraints = {}
                            requirement.soft_preferences = {}
                            requirement.known_conditions = {}
                            requirement.search_terms = []
                            requirement.completeness_score = 0
                            requirement.status = "draft"
                            await session.commit()
                        updates = {"user_message": args.description, "requirement_profile": {}}
                        result = {"answer": "需求描述已更新。", "description": args.description}
            if trace:
                await self.services.trace.tool_completed(*trace, {"result": result})
            await self._publish(state, "tool.completed", "tools", {"tool": name})
            await self._publish(state, "agent.completed", "tools", {"tool": name})
            return {
                "agent_route": "agent",
                "tool_iterations": iteration,
                "tool_results": [*state.get("tool_results", []), {"tool": name, **result}],
                **updates,
            }
        except Exception as exc:
            if trace:
                await self.services.trace.tool_failed(*trace, exc)
            await self._publish(state, "tool.failed", "tools", {"tool": name, "error": str(exc)})
            raise

    async def workflow_node(self, state):
        methods = {
            "requirement_analyst": self.requirement_node,
            "research_agent": self.research_node,
            "evidence_curator": self.curator_node,
            "research_ingestion_agent": self.research_ingestion_node,
            "achievement_evaluation_agent": self.achievement_evaluation_node,
            "assessment_matching_agent": self.assessment_node,
            "verification_agent": self.verifier_node,
            "final_report_agent": self.final_report_node,
        }
        step = state["workflow_step"]
        if step == "verification_agent" and state.get("task_type") == "matching":
            await self._publish(state, "agent.started", "constraint_validation")
            results = state.get("assessment_results", [])
            unknown = [
                c["constraint"]
                for a in results
                for c in a.get("constraint_checks", [])
                if c["status"] == "unknown"
            ]
            updates = {
                "verification": {
                    "decision": "passed" if results else "rejected",
                    "confidence": 0,
                    "recommended_action": "按原文证据复核未知约束",
                    "unknown_constraints": sorted(set(unknown)),
                },
                "next_step": "final_report_agent",
            }
            await self._publish(
                state, "agent.completed", "constraint_validation", updates["verification"]
            )
        elif step == "final_report_agent" and state.get("task_type") == "matching":
            await self._publish(state, "agent.started", "matching_report")
            lines, citations = ["成果匹配结果（按检索相关度排序）", ""], []
            labels = {"satisfied": "满足", "violated": "不满足", "unknown": "未知"}
            for result in state.get("assessment_results", []):
                lines.append(
                    f"{result['rank']}. {result['title']}：相关度 {result['final_score']:.1f}，"
                    f"硬约束{labels[result['eligibility']]}"
                )
                for check in result.get("constraint_checks", []):
                    basis = "原文证据核验" if check.get("method") == "evidence" else "规则核验"
                    line = (
                        f"- {check['constraint']}：{labels[check['status']]}"
                        f"（{basis}）。{check['reason']}"
                    )
                    if check.get("method") == "evidence" and check.get("quote"):
                        line += f"\n  > {check['quote'][:200]}"
                    lines.append(line)
                for chunk in result.get("evidence", [])[:2]:
                    number = len(citations) + 1
                    lines.append(f"- 原文 [{number}]：{chunk['text'][:250]}")
                    citations.append(
                        {**chunk, "title": result["title"], "url": "", "quote": chunk["text"]}
                    )
            if not state.get("assessment_results"):
                lines.append("当前范围内没有找到可核验的已审核成果。")
            lines.append("相关度仅用于排序；未知约束需要补充资料，不能视为满足。")
            updates = {"final_answer": "\n".join(lines), "citations": citations, "next_step": "end"}
            await self._publish(state, "agent.completed", "matching_report")
        else:
            updates = await methods[step](state)
        next_step = updates.get("next_step", "end")
        route = (
            "human_review"
            if next_step == "human_review"
            else ("respond" if next_step == "end" else "workflow")
        )
        return {**updates, "workflow_step": next_step, "agent_route": route}

    async def review_node(self, state):
        updates = await self.human_review_node(state)
        next_step = updates.get("next_step", "final_report_agent")
        if next_step == "supervisor":
            next_step = "requirement_analyst"
        return {**updates, "workflow_step": next_step, "agent_route": "workflow"}

    async def respond_node(self, state):
        if state.get("final_answer"):
            return {}
        results = state.get("tool_results", [])
        return {
            "final_answer": "\n\n".join(r["answer"] for r in results if r.get("answer"))
            or "未获得足够证据，请补充成果名称或工程需求。"
        }

    def build(self, checkpointer=None):
        graph = StateGraph(AgentState)
        for name, method in {
            "agent": self.agent_node,
            "tools": self.tools_node,
            "workflow": self.workflow_node,
            "human_review": self.review_node,
            "respond": self.respond_node,
        }.items():
            graph.add_node(name, method)
        graph.add_edge(START, "agent")
        routes = {name: name for name in ("agent", "tools", "workflow", "human_review", "respond")}
        for name in ("agent", "tools", "workflow", "human_review"):
            graph.add_conditional_edges(name, lambda state: state["agent_route"], routes)
        graph.add_edge("respond", END)
        return graph.compile(checkpointer=checkpointer)


def build_agent_graph(services, checkpointer=None, model=None):
    return HydroBridgeGraph(services, model=model).build(checkpointer)
