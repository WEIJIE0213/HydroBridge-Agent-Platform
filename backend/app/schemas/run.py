from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import TimestampedModel


class RunCreate(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=30_000)
    task_type: str = Field(
        default="auto",
        pattern="^(auto|research|requirement_analysis|evaluation|matching|qa)$",
    )
    context: dict[str, Any] = Field(default_factory=dict)


class RunResponse(TimestampedModel):
    thread_id: str
    run_sequence: int
    conversation_id: str
    user_id: str
    task_type: str
    status: str
    current_agent: str | None
    current_step: str | None
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    error_code: str | None
    error_message: str
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float
    latency_ms: int
    graph_version: str


class RunDecision(BaseModel):
    approval_id: str
    decision: str = Field(pattern="^(approve|edit|reject)$")
    comment: str = Field(default="", max_length=5_000)
    edited_payload: dict[str, Any] | None = None


class ConversationResponse(TimestampedModel):
    user_id: str
    title: str
    status: str
    summary: str


class ApprovalResponse(TimestampedModel):
    run_id: str
    requested_by_agent: str
    assigned_to: str | None
    risk_level: str
    reason: str
    payload: dict[str, Any]
    status: str
    decision: str | None
    decision_comment: str
    decided_by: str | None


class AgentStepResponse(TimestampedModel):
    run_id: str
    sequence: int
    agent_name: str
    node_name: str
    status: str
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    started_at: str
    finished_at: str | None
    latency_ms: int
    retry_count: int
    error_message: str


class ToolCallResponse(TimestampedModel):
    run_id: str
    step_id: str | None
    tool_name: str
    status: str
    input_payload: dict[str, Any]
    output_summary: dict[str, Any]
    latency_ms: int
    retry_count: int
    error_code: str | None
    error_message: str


class RunTraceResponse(BaseModel):
    run: RunResponse
    steps: list[AgentStepResponse]
    tool_calls: list[ToolCallResponse]
