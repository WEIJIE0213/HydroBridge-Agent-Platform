from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import TOOLS
from app.agents.model_gateway import ModelGateway
from app.core.config import settings
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


class CapturingStructuredRunnable(FakeStructuredRunnable):
    def __init__(self) -> None:
        self.messages: list[object] = []

    async def ainvoke(self, messages: list[object]) -> dict[str, Any]:
        self.messages = messages
        return await super().ainvoke(messages)


class FakeDeepSeekChatModel:
    def __init__(self) -> None:
        self.runnable = CapturingStructuredRunnable()

    def with_structured_output(
        self,
        _schema: type[BaseModel],
        *,
        method: str,
        include_raw: bool,
    ) -> CapturingStructuredRunnable:
        assert method == "json_mode"
        assert include_raw is True
        return self.runnable


class UsageFreeGateway(ModelGateway):
    async def _record_usage(self, *_: Any, **__: Any) -> None:
        return None


async def test_model_requests_survive_sequential_worker_event_loops(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")

    async def request():
        async with aclosing(UsageFreeGateway()) as gateway:
            return await gateway.text(
                "system", "user", session=None, run_id="test", agent_name="test"
            )

    def worker():
        # Match consecutive Celery tasks in the same prefork process. Each
        # asyncio.run creates and closes its own loop, with real HTTP keepalive.
        return [asyncio.run(request()), asyncio.run(request())]

    try:
        assert await asyncio.to_thread(worker) == ["ok", "ok"]
        assert len(requests) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_agent_decision_uses_native_function_calling():
    gateway = UsageFreeGateway()
    response = SimpleNamespace(
        tool_calls=[{"name": "ask_achievements", "args": {"query": "泵站监测"}}]
    )
    runnable = SimpleNamespace(ainvoke=AsyncMock(return_value=response))
    binding = Mock(return_value=runnable)
    gateway._model = SimpleNamespace(bind_tools=binding)
    call = await gateway.choose_tool(TOOLS, {"request": "泵站监测"}, session=None, run_id="test")
    binding.assert_called_once_with(TOOLS, parallel_tool_calls=False)
    assert call == {"name": "ask_achievements", "arguments": {"query": "泵站监测"}}


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
    gateway._responses_client = None
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


async def test_deepseek_json_mode_explicitly_requests_json(
    session: AsyncSession,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(settings, "llm_json_mode", True)
    gateway = UsageFreeGateway()
    gateway._responses_client = None
    model = FakeDeepSeekChatModel()
    gateway._model = model  # type: ignore[assignment]
    gateway._is_deepseek = True

    result = await gateway.structured(
        SampleResponse,
        "你是结构化信息提取助手。",
        "返回测试结果",
        session=session,
        run_id="unused-run",
        agent_name="test_agent",
    )

    assert result.answer == "ok"
    system_content = getattr(model.runnable.messages[0], "content", "")
    assert "JSON" in system_content
    assert '"answer"' in system_content
