"""One bounded decision loop dispatching to three compiled business subgraphs."""

from __future__ import annotations

import hashlib
import json
import re

from langgraph.graph import END, START, StateGraph
from sqlalchemy import and_, or_, select

from app.agents.state import AgentState
from app.agents.subgraphs import BusinessSubgraphs
from app.agents.tools import ACTION_GRAPHS, ACTION_TASKS, TOOL_SCHEMAS, TOOLS, achievement_scope
from app.core.config import settings
from app.db.models.domain import Achievement
from app.db.models.identity import User
from app.db.session import AsyncSessionFactory
from app.services.action_results import completed_action, persist_action
from app.services.conversation_context import ConversationContext, decision_observations
from app.services.workflow_records import bind_workflow

GRAPH_VERSION = "3.0.0-agent-subgraphs"
ACTION_LABELS = {
    "research_achievements": "成果搜寻与入库",
    "evaluate_achievement": "成果评价",
    "normalize_requirement": "需求分析",
    "match_achievements": "需求匹配",
    "update_requirement": "需求更新",
    "search_achievements": "成果问答",
    "get_achievement": "成果详情",
    "compare_achievements": "成果对比",
}
RESULT_FIELDS = (
    "requirement_profile",
    "requirement_description",
    "assessment_results",
    "achievement_evaluation",
    "verification",
    "ingestion_summary",
    "ingested_achievement_ids",
    "rag_result",
    "selected_achievement_ids",
    "scope_is_set",
    "user_message",
    "context",
)
RECORD_KEYS = ("requirement_id", "refinement_id", "match_id", "evaluation_id")


class HydroBridgeGraph(BusinessSubgraphs):
    async def prepare_context(self, state):
        context = state.get("context", {})
        if "achievement_ids" in context:
            ceiling = list(context["achievement_ids"])
        elif context.get("achievement_id"):
            ceiling = [str(context["achievement_id"])]
        elif state.get("scope_is_set"):
            ceiling = list(state.get("selected_achievement_ids", []))
        else:
            ceiling = None
        await self._publish(state, "agent.started", "context_summary")
        memory = await ConversationContext().prepare(state, self.model)
        await self._publish(state, "agent.completed", "context_summary", memory.get("memory", {}))
        return {
            **memory,
            "original_request": state.get("original_request", state["user_message"]),
            "requested_task_type": state.get("requested_task_type", state.get("task_type", "auto")),
            "scope_limit": ceiling,
            "selected_achievement_ids": ceiling if ceiling is not None else [],
            "scope_is_set": ceiling is not None,
            "tool_results": state.get("tool_results", []),
            "tool_iterations": state.get("tool_iterations", 0),
            "executed_actions": state.get("executed_actions", []),
            "stop_reason": "",
        }

    @staticmethod
    def _scope(state):
        return achievement_scope(state)

    @staticmethod
    def _offline_tool(state):
        message, scope = state["user_message"], achievement_scope(state)
        if any(word in message for word in ("联网", "上网", "调研", "搜寻")):
            return {"name": "research_achievements", "arguments": {"query": message}}
        if "规范化" in message or ("整理" in message and "需求" in message):
            return {"name": "normalize_requirement", "arguments": {"query": message}}
        if any(word in message for word in ("评价", "评估")):
            return {
                "name": "evaluate_achievement",
                "arguments": {"achievement_id": scope[0]} if scope and len(scope) == 1 else {},
            }
        if state.get("task_type") == "matching" or any(
            word in message for word in ("匹配", "推荐方案")
        ):
            return {"name": "match_achievements", "arguments": {"query": message}}
        if scope and len(scope) >= 2 and any(word in message for word in ("对比", "比较")):
            return {
                "name": "compare_achievements",
                "arguments": {
                    "achievement_ids": scope[:5],
                    "question": message,
                },
            }
        return {
            "name": "search_achievements",
            "arguments": {
                "query": message,
                "achievement_ids": scope,
            },
        }

    async def agent_node(self, state):
        await self._publish(state, "agent.started", "hydrobridge_agent")
        iterations = state.get("tool_iterations", 0)
        call, stop = {}, ""
        task = state.get("requested_task_type", "auto")
        initial_tools = {
            "research": "research_achievements",
            "evaluation": "evaluate_achievement",
            "requirement_analysis": "normalize_requirement",
            "matching": "match_achievements",
        }
        if iterations >= settings.agent_max_iterations:
            stop = "已达到本次动作次数上限，返回已完成的结果，本轮不再继续调用。"
        elif iterations == 0 and task in initial_tools:
            arguments = {"query": state["user_message"]}
            if task == "evaluation":
                scope = achievement_scope(state)
                arguments = {"achievement_id": scope[0]} if scope and len(scope) == 1 else {}
            call = {"name": initial_tools[task], "arguments": arguments}
        elif self.model.configured:
            async with AsyncSessionFactory() as session:
                call = await self.model.choose_tool(
                    TOOLS,
                    {
                        "request": state["original_request"],
                        "task": task,
                        "current_requirement_description": state.get("requirement_description", ""),
                        "history_summary": state.get("conversation_summary", ""),
                        "history": state.get("conversation_history", []),
                        "scope": achievement_scope(state),
                        "requirement": state.get("requirement_profile", {}),
                        "results": decision_observations(state.get("tool_results", [])),
                        "remaining_actions": settings.agent_max_iterations - iterations,
                    },
                    session=session,
                    run_id=state["run_id"],
                )
        elif iterations == 0:
            call = self._offline_tool(state)
        else:
            stop = "未配置决策模型，已返回可执行步骤的结果，未继续自动规划其他操作。"
        if not call and not iterations and not stop:
            call = self._offline_tool(state)
        if call and call.get("name") not in TOOL_SCHEMAS:
            raise ValueError("Agent requested an unknown tool")
        if call:
            args = TOOL_SCHEMAS[call["name"]][0].model_validate(call.get("arguments", {}))
            call = {"name": call["name"], "arguments": args.model_dump()}
        await self._publish(
            state,
            "agent.completed",
            "hydrobridge_agent",
            {
                "tool": call.get("name"),
                "iteration": iterations,
                "stop_reason": stop,
            },
        )
        return {
            "pending_tool": call,
            "agent_route": "dispatch" if call else "respond",
            "stop_reason": stop,
        }

    async def _evaluation_target(self, state, args):
        scope = achievement_scope(state)
        achievement_id = args.get("achievement_id")
        if not achievement_id and not args.get("title") and scope and len(scope) == 1:
            achievement_id = scope[0]
        async with AsyncSessionFactory() as session:
            user = await session.get(User, state["user_id"])
            access = and_(Achievement.organization_id.is_(None), Achievement.status == "approved")
            if state.get("organization_id"):
                access = or_(access, Achievement.organization_id == state["organization_id"])
            query = select(Achievement)
            if not (user and user.is_superuser):
                query = query.where(access)
            if scope is not None:
                query = query.where(Achievement.id.in_(scope))
            query = (
                query.where(Achievement.id == achievement_id)
                if achievement_id
                else query.where(
                    Achievement.title == str(args.get("title") or state["user_message"]).strip()
                )
            )
            candidates = list((await session.scalars(query.limit(2))).all())
            return candidates[0].id if len(candidates) == 1 else None

    async def dispatch(self, state):
        call = state["pending_tool"]
        name, args = call["name"], call["arguments"]
        serialized = json.dumps(
            {**call, "scope": achievement_scope(state)}, ensure_ascii=False, sort_keys=True
        )
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        if fingerprint in state.get("executed_actions", []):
            return {
                "action_route": "respond",
                "stop_reason": "检测到重复的相同操作，已停止重复调用并保留已有结果。",
            }
        iteration = state.get("tool_iterations", 0) + 1
        action_id = f"{state['run_id']}:{iteration}"
        cached = await completed_action(state["run_id"], action_id)
        if cached:
            if cached.get("fingerprint") != fingerprint:
                raise ValueError("重放动作与已保存的结果不一致，请发起新的任务")
            return {
                **cached.get("state_result", {}),
                "action_id": action_id,
                "action_fingerprint": fingerprint,
                "tool_iterations": iteration,
                "replayed_action": cached,
                "action_route": "collect",
            }
        context = {
            key: value
            for key, value in state.get("context", {}).items()
            if key
            not in {"domain_action", "evaluation_id", "refinement_id", "match_id", "achievement_id"}
        }
        message = args.get("query", args.get("description", state["user_message"]))
        updates = {
            "task_type": ACTION_TASKS[name],
            "user_message": message,
            "context": context,
            "tool_iterations": iteration,
            "action_id": action_id,
            "action_fingerprint": fingerprint,
            "action_status": "completed",
            "action_route": ACTION_GRAPHS[name],
            "replayed_action": {},
            "final_answer": "",
            "citations": [],
            "rag_result": {},
            "assessment_results": [],
            "achievement_evaluation": {},
            "verification": {},
            "search_queries": [],
            "search_results": [],
            "evidence_records": [],
            "rejected_sources": [],
            "ingestion_candidates": None,
            "ingestion_review_completed": False,
            "ingestion_approved_candidate_ids": [],
            "ingestion_reviewer_id": None,
            "ingested_achievement_ids": [],
            "ingestion_summary": {},
            "iteration_count": 0,
            "needs_human_review": False,
            "review_payload": {},
            "review_reason": "",
            "human_decision": {},
            # Working candidates are observations, not a new user-imposed boundary.
            # A later search/match must still be able to find other permitted results.
            "selected_achievement_ids": achievement_scope(state) or [],
            "scope_is_set": achievement_scope(state) is not None,
        }
        if name == "evaluate_achievement":
            achievement_id = await self._evaluation_target(state, args)
            if achievement_id is None:
                updates.update(
                    action_status="needs_input",
                    action_route="collect",
                    final_answer="请提供要评价的成果准确名称，或先检索并明确选定一项成果。",
                )
            else:
                context["achievement_id"] = achievement_id
        if (
            name
            in {
                "research_achievements",
                "evaluate_achievement",
                "normalize_requirement",
                "match_achievements",
            }
            and updates["action_status"] == "completed"
        ):
            updates["context"] = await bind_workflow(
                {**state, **updates}, ACTION_TASKS[name], message, context
            )
        trace = None
        if hasattr(self.services.trace, "tool_started"):
            trace = await self.services.trace.tool_started(state["run_id"], name, action_id, args)
        updates["action_trace"] = list(trace) if trace else None
        await self._publish(
            state,
            "tool.started",
            "hydrobridge_agent",
            {
                "tool": name,
                "action_id": action_id,
                "subgraph": ACTION_GRAPHS[name],
            },
        )
        return updates

    async def collect(self, state):
        entry = state.get("replayed_action")
        if not entry:
            choices = [
                {
                    "rank": item["rank"],
                    "id": item["achievement_id"],
                    "title": item["title"],
                    "eligibility": item.get("eligibility"),
                }
                for item in state.get("assessment_results", [])
            ]
            seen = {item["id"] for item in choices}
            for citation in state.get("citations", []):
                identity = citation.get("achievement_id")
                if identity and identity not in seen:
                    choices.append(
                        {
                            "rank": len(choices) + 1,
                            "id": identity,
                            "title": citation.get("title", ""),
                        }
                    )
                    seen.add(identity)
            for identity in state.get("ingested_achievement_ids", []):
                if identity not in seen:
                    choices.append({"rank": len(choices) + 1, "id": identity})
                    seen.add(identity)
            context = state.get("context", {})
            evaluated_id = context.get("achievement_id")
            if state.get("achievement_evaluation") and evaluated_id and evaluated_id not in seen:
                choices.append(
                    {
                        "rank": len(choices) + 1,
                        "id": evaluated_id,
                        "title": state["pending_tool"]["arguments"].get("title") or "",
                    }
                )
            entry = {
                "action_id": state["action_id"],
                "fingerprint": state["action_fingerprint"],
                "tool": state["pending_tool"]["name"],
                "arguments": state["pending_tool"]["arguments"],
                "status": state["action_status"],
                "answer": state.get("final_answer", ""),
                "citations": state.get("citations", []),
                "achievement_choices": choices,
                "record_ids": {key: context[key] for key in RECORD_KEYS if context.get(key)},
                "evidence_sufficient": state.get("rag_result", {}).get("sufficient"),
                "state_result": {key: state[key] for key in RESULT_FIELDS if key in state},
            }
            await persist_action(state, entry)
            if state.get("action_trace"):
                await self.services.trace.tool_completed(
                    *state["action_trace"],
                    {"status": entry["status"], "record_ids": entry["record_ids"]},
                )
            await self._publish(
                state,
                "tool.completed",
                "hydrobridge_agent",
                {
                    "tool": entry["tool"],
                    "action_id": entry["action_id"],
                    "status": entry["status"],
                },
            )
        results = [
            item
            for item in state.get("tool_results", [])
            if item["action_id"] != entry["action_id"]
        ]
        candidates = list(
            dict.fromkeys(
                [
                    *state.get("selected_achievement_ids", []),
                    *(
                        choice["id"]
                        for item in [*results, entry]
                        for choice in item.get("achievement_choices", [])
                    ),
                ]
            )
        )
        ceiling = state.get("scope_limit")
        if ceiling is not None:
            candidates = [identity for identity in candidates if identity in ceiling]
        return {
            "tool_results": [*results, entry],
            "selected_achievement_ids": candidates,
            "scope_is_set": ceiling is not None
            or state.get("scope_is_set", False)
            or bool(candidates),
            "executed_actions": [*state.get("executed_actions", []), state["action_fingerprint"]],
            "action_trace": None,
            "replayed_action": {},
        }

    async def respond_node(self, state):
        answers, citations, restored, context = [], [], {}, dict(state.get("context", {}))
        results = state.get("tool_results", [])
        for index, item in enumerate(results):
            if item.get("status") == "needs_input" and any(
                later["tool"] == item["tool"] and later.get("status") == "completed"
                for later in results[index + 1 :]
            ):
                continue
            offset = len(citations)
            local_citations = item.get("citations", [])
            citation_count = len(local_citations)

            def renumber(match, base=offset, count=citation_count):
                number = int(match[1])
                return f"[{base + number}]" if 1 <= number <= count else match[0]

            # Only renumber the references inserted by our renderers, never numbers
            # or reference marks inside verbatim source quotations.
            answer = item.get("answer", "")
            if item["tool"] in {"search_achievements", "compare_achievements"}:
                answer = re.sub(r"\[(\d+)\](?=\n\n>)", renumber, answer)
            elif item["tool"] == "match_achievements":
                answer = re.sub(r"(?m)(?<=^- 原文 )\[(\d+)\](?=：)", renumber, answer)
            if answer:
                label = ACTION_LABELS.get(item["tool"], item["tool"])
                answers.append(f"### {label}\n\n{answer}" if len(results) > 1 else answer)
            citations.extend(local_citations)
            context.update(item.get("record_ids", {}))
            for key in (
                "assessment_results",
                "achievement_evaluation",
                "verification",
                "ingestion_summary",
                "ingested_achievement_ids",
                "rag_result",
            ):
                value = item.get("state_result", {}).get(key)
                if value:
                    restored[key] = value
        if state.get("stop_reason"):
            answers.append(state["stop_reason"])
        return {
            **restored,
            "context": context,
            "final_answer": "\n\n".join(answers) or "未获得足够证据，请补充成果名称或工程需求。",
            "citations": citations,
        }

    def build(self, checkpointer=None):
        graph = StateGraph(AgentState)
        for name, node in {
            "prepare_context": self.prepare_context,
            "agent": self.agent_node,
            "dispatch": self.dispatch,
            "collect": self.collect,
            "respond": self.respond_node,
            "achievement_pipeline": self.achievement_graph(),
            "requirement_pipeline": self.requirement_graph(),
            "retrieval_qa_pipeline": self.qa_graph(),
        }.items():
            graph.add_node(name, node)
        graph.add_edge(START, "prepare_context")
        graph.add_edge("prepare_context", "agent")
        graph.add_conditional_edges(
            "agent",
            lambda s: s["agent_route"],
            {
                "dispatch": "dispatch",
                "respond": "respond",
            },
        )
        graph.add_conditional_edges(
            "dispatch",
            lambda s: s["action_route"],
            {
                name: name
                for name in (
                    "achievement_pipeline",
                    "requirement_pipeline",
                    "retrieval_qa_pipeline",
                    "collect",
                    "respond",
                )
            },
        )
        for name in ("achievement_pipeline", "requirement_pipeline", "retrieval_qa_pipeline"):
            graph.add_edge(name, "collect")
        graph.add_edge("collect", "agent")
        graph.add_edge("respond", END)
        return graph.compile(checkpointer=checkpointer)


def build_agent_graph(services, checkpointer=None, model=None, *, version=GRAPH_VERSION):
    if version != GRAPH_VERSION:
        # Already-paused v2 runs must resume their original node layout.
        from app.agents.legacy_graph import build_agent_graph as build_legacy_graph

        return build_legacy_graph(services, checkpointer=checkpointer, model=model)
    return HydroBridgeGraph(services, model=model).build(checkpointer)
