from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.model_gateway import ModelGateway
from app.db.models.identity import User
from app.db.models.runtime import AgentRun, AgentStep, Conversation, ModelUsage


class SampleResponse(BaseModel):
    answer: str


class FakeStructuredRunnable:
    async def ainvoke(self, _messages: list[object]) -> dict[str, Any]:
        raw = SimpleNamespace(
            usage_metadata={
                "input_tokens": 17,
                "output_tokens": 5,
                "input_token_details": {"cache_read": 3},
            },
            response_metadata={"model_name": "test-model"},
        )
        return {
            "raw": raw,
            "parsed": SampleResponse(answer="ok"),
            "parsing_error": None,
        }


class FakeChatModel:
    def with_structured_output(
        self,
        _schema: type[BaseModel],
        *,
        method: str,
        include_raw: bool,
    ) -> FakeStructuredRunnable:
        assert method == "json_schema"
        assert include_raw is True
        return FakeStructuredRunnable()


async def test_model_gateway_records_usage_and_updates_run_totals(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    conversation = Conversation(user_id=seeded_user.id, title="usage test")
    session.add(conversation)
    await session.flush()
    run = AgentRun(
        thread_id=conversation.id,
        run_sequence=1,
        conversation_id=conversation.id,
        user_id=seeded_user.id,
        task_type="qa",
        status="running",
    )
    session.add(run)
    await session.flush()
    step = AgentStep(
        run_id=run.id,
        sequence=1,
        agent_name="supervisor",
        node_name="supervisor",
        status="running",
        started_at="2026-07-26T00:00:00+00:00",
    )
    session.add(step)
    await session.commit()

    gateway = ModelGateway()
    gateway._model = FakeChatModel()  # type: ignore[assignment]
    result = await gateway.structured(
        SampleResponse,
        "system",
        "user",
        session=session,
        run_id=run.id,
        agent_name="supervisor",
    )

    assert result.answer == "ok"
    usage = (await session.execute(select(ModelUsage))).scalar_one()
    assert usage.step_id == step.id
    assert usage.model_name == "test-model"
    assert usage.input_tokens == 17
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 3
    await session.refresh(run)
    assert run.total_input_tokens == 17
    assert run.total_output_tokens == 5
