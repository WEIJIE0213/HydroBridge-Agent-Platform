from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.graph import build_agent_graph
from app.core.exceptions import RunCancelledError
from app.services.retrieval import RetrievalHit


class FakeEvents:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        **_: Any,
    ) -> str:
        self.items.append((run_id, event_type, data or {}))
        return str(len(self.items))


class FakeTrace:
    async def agent_started(self, *_: Any, **__: Any) -> str:
        return "step"

    async def agent_completed(self, *_: Any, **__: Any) -> None:
        return None


class FakeRuntimeRedis:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    async def get(self, _: str) -> str | None:
        return "1" if self.cancelled else None


class FakeRedis:
    def __init__(self, cancelled: bool = False) -> None:
        self.runtime = FakeRuntimeRedis(cancelled)


class FakeSearch:
    configured = False


class FakeRetrieval:
    async def search(self, *_: Any, **__: Any) -> list[RetrievalHit]:
        return [
            RetrievalHit(
                achievement_id="achievement-1",
                title="农村供水智能监测技术",
                abstract="适用于偏远供水工程的低功耗监测。",
                score=0.88,
                reranker_score=0.91,
                payload={"status": "approved", "maturity_level": 8},
            )
        ]


class FakeServices:
    def __init__(self, cancelled: bool = False) -> None:
        self.events = FakeEvents()
        self.trace = FakeTrace()
        self.redis = FakeRedis(cancelled)
        self.search = FakeSearch()
        self.retrieval = FakeRetrieval()


def initial_state(message: str) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "thread_id": "thread-1",
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "organization_id": "organization-1",
        "task_type": "matching",
        "user_message": message,
        "context": {},
        "plan": [],
        "visited_agents": [],
        "iteration_count": 0,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_matching_graph_runs_to_verified_report() -> None:
    services = FakeServices()
    graph = build_agent_graph(services, checkpointer=MemorySaver())  # type: ignore[arg-type]
    result = await graph.ainvoke(
        initial_state(
            "为高寒地区农村供水工程匹配低维护、成熟度高的监测技术成果"
        ),
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "HydroBridge AI 分析结果" in result["final_answer"]
    assert result["verification"]["decision"] == "passed"
    assert result["assessment_results"][0]["title"] == "农村供水智能监测技术"
    assert "verification_agent" in result["visited_agents"]
    event_types = [item[1] for item in services.events.items]
    assert "plan.created" in event_types
    assert event_types.count("agent.completed") >= 5


@pytest.mark.asyncio
async def test_graph_interrupts_and_resumes_after_clarification() -> None:
    services = FakeServices()
    graph = build_agent_graph(services, checkpointer=MemorySaver())  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": "thread-review"}}

    interrupted = await graph.ainvoke(initial_state("供水匹配"), config=config)
    assert interrupted["__interrupt__"]

    resumed = await graph.ainvoke(
        Command(
            resume={
                "decision": "edit",
                "edited_payload": {
                    "message": "为农村供水泵站匹配低维护监测技术，要求不停水施工"
                },
            }
        ),
        config=config,
    )
    assert resumed["final_answer"]
    assert resumed["human_decision"]["decision"] == "edit"


@pytest.mark.asyncio
async def test_graph_observes_cancellation_before_agent_work() -> None:
    services = FakeServices(cancelled=True)
    graph = build_agent_graph(services)  # type: ignore[arg-type]

    with pytest.raises(RunCancelledError):
        await graph.ainvoke(initial_state("匹配农村供水成果"))
