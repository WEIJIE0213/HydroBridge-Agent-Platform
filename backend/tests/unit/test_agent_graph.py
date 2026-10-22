from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpoint import checkpointer_context
from app.agents.graph import HydroBridgeGraph, build_agent_graph
from app.agents.schemas import (
    CuratedAchievement,
    CuratedAchievementBatch,
    EvidenceClaim,
    MatchAssessment,
    MatchAssessmentBatch,
    ResearchPlan,
)
from app.agents.specialists import AssessmentMatchingAgent
from app.core.exceptions import RunCancelledError
from app.db.models.domain import Achievement, Requirement
from app.db.models.identity import User
from app.services.agent_runtime import AgentRuntime
from app.services.retrieval import RetrievalHit, RetrievalService
from app.services.run_trace import RunTraceService


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
    async def search(self, *args: Any, **kwargs: Any) -> list[RetrievalHit]:
        return await RetrievalService(None).search(*args, **kwargs)


@pytest_asyncio.fixture
async def matching_catalog(session: AsyncSession) -> Achievement:
    achievement = Achievement(
        title="农村供水智能监测技术",
        normalized_title="农村供水智能监测技术",
        abstract="适用于偏远供水工程的低功耗监测。",
        maturity_level=8,
        status="approved",
    )
    session.add(achievement)
    await session.commit()
    return achievement


class FakeResearchAgent:
    name = "research_agent"

    async def research(self, *_: Any, **__: Any) -> tuple[ResearchPlan, list[dict[str, Any]]]:
        return ResearchPlan(queries=["泵站预测性维护科技成果"]), [
            {
                "title": "泵站设备预测性维护技术",
                "url": "https://example.gov.cn/results/pump-maintenance",
                "content": "系统融合振动、电流和温度信号识别泵组异常。",
                "source_type": "official_result_catalog",
                "authority_level": "official",
                "authority_score": 0.96,
                "relevance_score": 0.95,
                "focus_coverage": 0.9,
                "query_coverage": 0.85,
                "temporal_status": "in_range",
                "low_evidence_signals": [],
                "marketing_signals": [],
                "accepted": True,
            }
        ]


class FakeGreyZoneCurator:
    name = "evidence_curator"

    async def curate(self, *_: Any, **__: Any) -> CuratedAchievementBatch:
        url = "https://example.gov.cn/results/pump-maintenance"
        return CuratedAchievementBatch(
            achievements=[
                CuratedAchievement(
                    title="泵站设备预测性维护技术",
                    normalized_title="泵站设备预测性维护技术",
                    organizations=[],
                    abstract="融合多源运行信号识别泵组故障。",
                    technical_principle="通过时序特征与异常检测模型进行预测性维护。",
                    application_scenarios=["泵站运维"],
                    maturity_level=8,
                    advantages=["降低非计划停机"],
                    limitations=["需要现场基线数据"],
                    evidence=[
                        EvidenceClaim(
                            field="technical_principle",
                            claim="采用多源异常检测",
                            evidence_text="系统融合振动、电流和温度信号识别泵组异常。",
                            source_url=url,
                            support_type="direct",
                            confidence=0.94,
                        )
                    ],
                    source_urls=[url],
                    evidence_score=0.9,
                )
            ]
        )


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


def test_matching_headline_score_is_derived_from_dimensions() -> None:
    batch = MatchAssessmentBatch(
        results=[
            MatchAssessment(
                achievement_id="achievement-1",
                title="候选成果",
                rank=1,
                final_score=99,
                scenario_fit=60,
                effectiveness=79,
                maturity=78,
                evidence_quality=70,
                cost_fit=60,
                implementation_risk=41,
            )
        ]
    )

    normalized = AssessmentMatchingAgent.normalize_batch(batch)

    assert normalized.results[0].final_score == 67.9


@pytest.mark.asyncio
async def test_matching_graph_runs_to_verified_report(matching_catalog: Achievement) -> None:
    services = FakeServices()
    graph = build_agent_graph(services, checkpointer=MemorySaver())  # type: ignore[arg-type]
    result = await graph.ainvoke(
        initial_state("为高寒地区农村供水工程匹配低维护、成熟度高的监测技术成果"),
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "成果匹配结果（按检索相关度排序）" in result["final_answer"]
    assert result["verification"]["decision"] == "passed"
    assert result["assessment_results"][0]["title"] == "农村供水智能监测技术"
    assert result["selected_achievement_ids"] == [matching_catalog.id]
    assert result["assessment_results"][0]["score_kind"] == "retrieval_relevance"
    assert result["citations"][0]["chunk_id"]
    event_types = [item[1] for item in services.events.items]
    assert "plan.created" not in event_types
    assert event_types.count("agent.completed") >= 5


@pytest.mark.parametrize("selection", ["catalog", "empty", "missing"])
async def test_followup_matching_preserves_prior_scope(matching_catalog, selection):
    selected = (
        [matching_catalog.id]
        if selection == "catalog"
        else []
        if selection == "empty"
        else ["missing-achievement"]
    )
    state = initial_state("继续匹配农村供水监测技术")
    state.update(selected_achievement_ids=selected, scope_is_set=True)
    result = await HydroBridgeGraph(FakeServices()).assessment_node(state)
    assert result["scope_is_set"] is True
    assert result["selected_achievement_ids"] == selected
    assert [item["achievement_id"] for item in result["assessment_results"]] == (
        selected if selection == "catalog" else []
    )


@pytest.mark.asyncio
async def test_graph_interrupts_and_resumes_after_clarification() -> None:
    services = FakeServices()
    graph = build_agent_graph(services, checkpointer=MemorySaver())  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": "thread-review"}}

    interrupted = await graph.ainvoke(initial_state("供水匹配"), config=config)
    assert interrupted["__interrupt__"]
    payload = interrupted["__interrupt__"][0].value["payload"]
    assert payload["type"] == "clarification"
    assert payload["title"] == "需求信息还不完整"
    assert payload["resume_action"] == "requirement_matching"
    assert payload["original_message"] == "供水匹配"
    assert payload["fields"][0]["key"] == "scenario_and_problem"
    assert payload["fields"][0]["required"] is True
    assert len(payload["fields"][0]["suggestions"]) == 3

    resumed = await graph.ainvoke(
        Command(
            resume={
                "decision": "edit",
                "edited_payload": {"message": "为农村供水泵站匹配低维护监测技术，要求不停水施工"},
            }
        ),
        config=config,
    )
    assert resumed["final_answer"]
    assert resumed["human_decision"]["decision"] == "edit"
    assert resumed["visited_agents"].count("requirement_analyst") == 2


@pytest.mark.asyncio
async def test_test_runtime_reuses_checkpointer_for_http_resume() -> None:
    async with checkpointer_context() as first:
        pass
    async with checkpointer_context() as second:
        pass

    assert first is second


def test_clarification_resume_keeps_enriched_message_for_recovery() -> None:
    message = AgentRuntime._clarification_recovery_message(
        {
            "decision": "edit",
            "review_type": "clarification",
            "edited_payload": {"message": "农村供水泵站，要求不停水改造"},
        }
    )

    assert message == "农村供水泵站，要求不停水改造"


@pytest.mark.asyncio
async def test_approved_clarification_continues_matching_workflow(
    matching_catalog: Achievement,
) -> None:
    services = FakeServices()
    graph = build_agent_graph(services, checkpointer=MemorySaver())  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": "thread-approved-clarification"}}

    interrupted = await graph.ainvoke(initial_state("供水匹配"), config=config)
    assert interrupted["__interrupt__"]

    resumed = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config=config,
    )

    assert resumed["assessment_results"]
    assert resumed["assessment_results"][0]["title"] == "农村供水智能监测技术"
    assert "match_achievements" in resumed["visited_agents"]


@pytest.mark.asyncio
async def test_research_grey_zone_interrupts_once_and_selected_result_is_ingested(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    services = FakeServices()
    workflow = HydroBridgeGraph(services)  # type: ignore[arg-type]
    workflow.research = FakeResearchAgent()  # type: ignore[assignment]
    workflow.curator = FakeGreyZoneCurator()  # type: ignore[assignment]
    graph = workflow.build(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "thread-research-review"}}
    state = initial_state("联网查找泵站预测性维护科技成果")
    state.update(
        {
            "task_type": "research",
            "organization_id": seeded_user.organization_id,
            "user_id": seeded_user.id,
        }
    )

    interrupted = await graph.ainvoke(state, config=config)
    interrupt_payload = interrupted["__interrupt__"][0].value
    assert interrupt_payload["payload"]["type"] == "research_ingestion"
    candidates = interrupt_payload["payload"]["candidates"]
    assert len(candidates) == 1

    resumed = await graph.ainvoke(
        Command(
            resume={
                "decision": "approve",
                "reviewer_id": seeded_user.id,
                "edited_payload": {
                    "approved_candidate_ids": [candidates[0]["candidate_id"]],
                },
            }
        ),
        config=config,
    )

    assert resumed["ingestion_summary"]["human_approved"] == 1
    assert resumed["ingestion_summary"]["total_ingested"] == 1
    assert resumed["final_answer"]
    assert not resumed.get("__interrupt__")
    assert await session.scalar(select(func.count()).select_from(Achievement)) == 1


@pytest.mark.asyncio
async def test_graph_observes_cancellation_before_agent_work() -> None:
    services = FakeServices(cancelled=True)
    graph = build_agent_graph(services)  # type: ignore[arg-type]

    with pytest.raises(RunCancelledError):
        await graph.ainvoke(initial_state("匹配农村供水成果"))


@pytest.mark.asyncio
async def test_intrinsic_achievement_evaluation_uses_dedicated_dimensions(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    achievement = Achievement(
        organization_id=seeded_user.organization_id,
        title="高寒地区农村供水低功耗监测技术",
        normalized_title="高寒地区农村供水低功耗监测技术",
        abstract="已形成低功耗传感、异常识别和远程运维方案。",
        technical_principle="通过边缘采集和时序模型识别供水异常。",
        application_scenarios=["高寒农村供水"],
        maturity_level=8,
        metrics=[{"name": "续航时间", "value": "24个月"}],
        advantages=["低功耗", "远程运维"],
        evidence_score=0.82,
        status="approved",
    )
    session.add(achievement)
    await session.commit()

    services = FakeServices()
    state = initial_state("评价这项成果")
    state.update(
        {
            "task_type": "evaluation",
            "context": {"achievement_id": achievement.id},
        }
    )
    graph = build_agent_graph(services, checkpointer=MemorySaver())  # type: ignore[arg-type]
    result = await graph.ainvoke(
        state,
        config={"configurable": {"thread_id": "thread-evaluation"}},
    )

    evaluation = result["achievement_evaluation"]
    assert 0 <= evaluation["overall_score"] <= 100
    assert set(evaluation["dimension_scores"]) == {
        "innovation",
        "effectiveness",
        "maturity",
        "evidence_quality",
        "promotion_value",
        "implementation_feasibility",
    }
    assert result["verification"]["decision"] == "passed"


async def test_runtime_followup_remembers_scope_but_new_conversation_does_not(
    session: AsyncSession,
    seeded_user: User,
    matching_catalog: Achievement,
) -> None:
    services = FakeServices()
    services.trace = RunTraceService()
    services.object_storage = SimpleNamespace(artifact_bucket="reports", put_bytes=AsyncMock())
    services.redis.runtime.expire = AsyncMock()
    services.events.stream_key = lambda run_id: f"test:{run_id}"
    runtime = AgentRuntime(services)
    first = await runtime.create_run(
        session,
        user_id=seeded_user.id,
        organization_id=seeded_user.organization_id,
        message="为农村供水工程匹配低功耗智能监测技术",
        task_type="matching",
        context={},
    )
    await runtime.execute(first.id)
    await session.refresh(first)
    assert first.status == "completed", first.error_message
    assert first.output_payload["selected_achievement_ids"] == [matching_catalog.id]
    seen_scopes = []
    original_search = services.retrieval.search

    async def capture_search(*args, **kwargs):
        seen_scopes.append(kwargs.get("achievement_ids"))
        return await original_search(*args, **kwargs)

    services.retrieval.search = capture_search
    followup = await runtime.create_run(
        session,
        user_id=seeded_user.id,
        organization_id=seeded_user.organization_id,
        message="它的技术原理是什么",
        task_type="qa",
        context={},
        conversation_id=first.conversation_id,
    )
    await runtime.execute(followup.id)
    await session.refresh(followup)
    assert followup.status == "completed", followup.error_message
    assert seen_scopes == [[matching_catalog.id]]
    assert followup.run_sequence == 2
    assert followup.output_payload["rag"]["mode"] == "extractive"
    assert followup.output_payload["scope_is_set"]
    fresh = await runtime.create_run(
        session,
        user_id=seeded_user.id,
        organization_id=seeded_user.organization_id,
        message="查询其他工程监测技术",
        task_type="qa",
        context={},
    )
    await runtime.execute(fresh.id)
    assert seen_scopes[-1] is None


async def test_update_requirement_clears_stale_constraints_and_checks_owner(session, seeded_user):
    item = Requirement(
        created_by=seeded_user.id,
        title="旧需求",
        raw_input="原需求",
        normalized_description="原规范需求",
        hard_constraints={"min_maturity_level": 9},
        completeness_score=0.9,
        status="confirmed",
    )
    session.add(item)
    await session.commit()
    workflow = HydroBridgeGraph(FakeServices())
    state = initial_state("更新需求为泵站低功耗监测")
    state.update(
        {
            "user_id": seeded_user.id,
            "context": {"requirement_id": item.id},
            "pending_tool": {
                "name": "update_requirement",
                "arguments": {"description": "泵站低功耗监测"},
            },
        }
    )
    result = await workflow.tools_node(state)
    await session.refresh(item)
    assert item.raw_input == "泵站低功耗监测"
    assert item.hard_constraints == {} and item.completeness_score == 0 and item.status == "draft"
    assert result["requirement_profile"] == {}
    state["user_id"] = "other-user"
    with pytest.raises(ValueError, match="无权修改"):
        await workflow.tools_node(state)


async def test_matching_preserves_explicit_empty_scope(matching_catalog):
    state = initial_state("为农村供水工程匹配低功耗智能监测技术")
    state["context"] = {"achievement_ids": []}
    result = await build_agent_graph(FakeServices()).ainvoke(state)
    assert result["assessment_results"] == []
    assert result["selected_achievement_ids"] == [] and result["scope_is_set"] is True
