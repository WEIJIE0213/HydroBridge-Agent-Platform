from __future__ import annotations

from typing import Any, Literal, TypedDict

TaskType = Literal[
    "auto",
    "research",
    "requirement_analysis",
    "evaluation",
    "matching",
    "qa",
]


class AgentState(TypedDict, total=False):
    run_id: str
    thread_id: str
    conversation_id: str
    user_id: str
    organization_id: str | None
    task_type: TaskType
    user_message: str
    context: dict[str, Any]

    plan: list[str]
    next_step: str
    iteration_count: int
    visited_agents: list[str]

    requirement_profile: dict[str, Any]
    clarification: dict[str, Any]
    search_queries: list[str]
    search_results: list[dict[str, Any]]
    evidence_records: list[dict[str, Any]]
    rejected_sources: list[dict[str, str]]
    retrieval_hits: list[dict[str, Any]]
    assessment_results: list[dict[str, Any]]
    verification: dict[str, Any]

    needs_human_review: bool
    review_reason: str
    review_payload: dict[str, Any]
    human_decision: dict[str, Any]

    final_answer: str
    citations: list[dict[str, Any]]
    errors: list[dict[str, Any]]
