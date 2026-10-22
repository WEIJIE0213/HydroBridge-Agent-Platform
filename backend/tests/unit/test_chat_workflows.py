from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agents.graph import HydroBridgeGraph
from app.db.models.domain import (
    Achievement,
    MatchItem,
    MatchRun,
    Requirement,
    RequirementRefinement,
)
from app.db.models.identity import User
from app.db.models.runtime import AgentRun, Conversation
from app.services.agent_runtime import AgentRuntime
from app.services.workflow_records import bind_workflow


@pytest.mark.parametrize(
    ("message", "tool"),
    [
        ("联网搜寻泵站监测成果", "research_achievements"),
        ("先整理需求，暂时不要匹配", "normalize_requirement"),
        ("先帮我整理农村供水泵站的改造需求，暂时不要匹配", "normalize_requirement"),
        ("评价这项成果", "evaluate_achievement"),
        ("匹配农村供水监测技术", "match_achievements"),
    ],
)
def test_offline_chat_exposes_each_business_workflow(message, tool):
    call = HydroBridgeGraph._offline_tool({"user_message": message, "task_type": "auto"})
    assert call["name"] == tool


async def chat_state(session, user):
    conversation = Conversation(user_id=user.id, title="聊天业务关联")
    session.add(conversation)
    await session.flush()
    run = AgentRun(
        thread_id=conversation.id,
        conversation_id=conversation.id,
        user_id=user.id,
        task_type="auto",
        status="running",
        input_payload={"message": "农村供水低功耗监测", "context": {}},
    )
    session.add(run)
    await session.commit()
    return {
        "run_id": run.id,
        "user_id": user.id,
        "organization_id": user.organization_id,
        "user_message": "农村供水低功耗监测",
        "context": {},
    }


async def test_chat_normalization_creates_one_record_and_no_match(session, seeded_user):
    state = await chat_state(session, seeded_user)
    first = await bind_workflow(state, "requirement_analysis", state["user_message"], {})
    second = await bind_workflow(state, "requirement_analysis", state["user_message"], {})
    assert first["refinement_id"] == second["refinement_id"]
    assert await session.scalar(select(func.count()).select_from(Requirement)) == 1
    assert await session.scalar(select(func.count()).select_from(RequirementRefinement)) == 1
    assert await session.scalar(select(func.count()).select_from(MatchRun)) == 0
    record = await session.get(RequirementRefinement, first["refinement_id"])
    assert record.agent_run_id == state["run_id"]


async def test_chat_match_persists_candidates_for_management_page(session, seeded_user):
    state = await chat_state(session, seeded_user)
    context = await bind_workflow(state, "matching", state["user_message"], {})
    achievement = Achievement(
        title="农村供水监测技术", normalized_title="农村供水监测技术", status="approved"
    )
    session.add(achievement)
    await session.flush()
    run = await session.get(AgentRun, state["run_id"])
    await session.refresh(run)
    result = {
        "user_message": state["user_message"],
        "requirement_profile": {"title": "供水监测", "completeness_score": 0.8},
        "assessment_results": [{
            "achievement_id": achievement.id, "rank": 1, "final_score": 85,
            "eligibility": "unknown", "constraint_checks": [],
        }],
    }
    await AgentRuntime(SimpleNamespace())._persist_domain_result(session, run, result)
    await session.flush()
    match = await session.get(MatchRun, context["match_id"])
    candidate = await session.scalar(select(MatchItem).where(MatchItem.match_id == match.id))
    assert match.status == "completed" and match.candidate_count == 1
    assert candidate.achievement_id == achievement.id
    assert candidate.decision_status == "pending"
    requirement = await session.get(Requirement, context["requirement_id"])
    assert requirement.title == "供水监测"


async def test_chat_cannot_bind_another_users_requirement(session, seeded_user):
    state = await chat_state(session, seeded_user)
    other = User(email="other@example.com", display_name="其他用户", password_hash="unused")
    session.add(other)
    await session.flush()
    requirement = Requirement(created_by=other.id, title="其他需求", raw_input="内部资料")
    session.add(requirement)
    await session.commit()
    with pytest.raises(ValueError, match="无权修改"):
        await bind_workflow(
            state, "matching", state["user_message"], {"requirement_id": requirement.id}
        )


async def test_ambiguous_evaluation_asks_for_target_without_creating_a_record(session, seeded_user):
    state = await chat_state(session, seeded_user)
    graph = HydroBridgeGraph(SimpleNamespace(), model=SimpleNamespace(configured=False))
    result = await graph._start_workflow(state, "evaluation", "评价一下")
    assert result["agent_route"] == "respond"
    assert "准确名称" in result["final_answer"]
    run = await session.get(AgentRun, state["run_id"])
    await session.refresh(run)
    assert "evaluation_id" not in run.input_payload["context"]
