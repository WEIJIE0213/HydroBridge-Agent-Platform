"""Contract regressions for the three business tools."""

from copy import deepcopy

import pytest

from app.agents.model_gateway import ModelGateway
from app.agents.requirement_patch import RequirementChanges, merge_requirement
from app.agents.tools import TOOLS, normalize_public_call
from app.services.conversation_context import decision_observations
from app.services.retrieval import RetrievalResults, retrieval_notices


@pytest.fixture
def state():
    return {
        "scope_limit": ["a", "b"],
        "active_candidates": [
            {"rank": 1, "id": "b", "title": "第二类技术"},
            {"rank": 2, "id": "a", "title": "第一类技术"},
        ],
        "requirement_profile": {
            "title": "泵站监测",
            "scenario": "农村泵站",
            "problem": "巡检成本高",
            "normalized_description": "农村泵站要求预算10万元内且不停水改造",
            "hard_constraints": {"budget": {"max": 10, "unit": "万元"}, "shutdown": False},
            "soft_preferences": {"maintenance": "low"},
            "completeness_score": 0.8,
            "missing_critical_fields": [],
        },
    }


def test_refine_preserves_unmentioned_constraints_and_does_not_mutate_source(state):
    profile = state["requirement_profile"]
    before = deepcopy(profile)
    patch = RequirementChanges(hard_constraints={"budget": {"max": 20, "unit": "万元"}})
    merged = merge_requirement(profile, patch)
    assert profile == before
    assert merged["hard_constraints"] == {
        "budget": {"max": 20, "unit": "万元"},
        "shutdown": False,
    }
    assert merged["soft_preferences"] == before["soft_preferences"]
    assert merged["scenario"] == before["scenario"]


def test_deletion_only_removes_named_constraint(state):
    merged = merge_requirement(
        state["requirement_profile"], RequirementChanges(hard_constraints={"budget": None})
    )
    assert merged["hard_constraints"] == {"shutdown": False}
    with pytest.raises(ValueError):
        merge_requirement(state["requirement_profile"], RequirementChanges(hard_constraints=None))


@pytest.mark.parametrize("patch", [{}, {"hard_constraints": {"min_maturity_level": 12}}])
def test_refine_rejects_empty_and_invalid_patches(state, patch):
    with pytest.raises(ValueError):
        merge_requirement(state["requirement_profile"], RequirementChanges.model_validate(patch))


def test_rank_resolution_uses_display_order_and_keeps_scope(state):
    call = normalize_public_call(
        {
            "name": "ask_achievements",
            "arguments": {
                "mode": "detail",
                "achievement_ranks": [2],
            },
        },
        state,
    )
    assert call["arguments"]["achievement_id"] == "a"
    state["scope_limit"] = ["b"]
    with pytest.raises(ValueError):
        normalize_public_call(
            {
                "name": "ask_achievements",
                "arguments": {
                    "mode": "detail",
                    "achievement_ranks": [2],
                },
            },
            state,
        )


def test_refine_can_stop_after_analysis(state):
    call = normalize_public_call(
        {
            "name": "match_achievements",
            "arguments": {
                "mode": "refine",
                "query": "取消预算限制，暂不匹配",
                "match_after_refine": False,
                "patch": {"hard_constraints": {"budget": None}},
            },
        },
        state,
    )
    assert call["name"] == "normalize_requirement"
    assert call["arguments"]["requirement_patch"] == {"hard_constraints": {"budget": None}}


def test_empty_fallback_results_still_expose_degradation():
    hits = RetrievalResults(backend="local_fallback", fallback_reason="ConnectionError")
    assert not hits
    assert retrieval_notices(hits)
    assert not retrieval_notices(RetrievalResults(backend="elasticsearch"))


def test_no_tool_clarification_does_not_become_a_fact_report():
    assert ModelGateway._clarification("CLARIFY:新的预算上限是多少？", TOOLS) == {
        "question": "新的预算上限是多少？",
    }
    assert ModelGateway._clarification("DONE", TOOLS) == {}
    assert ModelGateway._clarification("这项成果一定满足预算", TOOLS) == {}


def test_invalid_json_arguments_can_return_as_an_observation():
    observation = decision_observations(
        [
            {
                "action_id": "run:1",
                "tool": "invalid_tool",
                "status": "error",
                "arguments": {},
                "answer": "参数不是有效JSON",
                "public_call": {"name": "ask_achievements", "arguments": "{broken"},
            }
        ]
    )[0]
    assert observation["status"] == "error"
    assert observation["mode"] is None
