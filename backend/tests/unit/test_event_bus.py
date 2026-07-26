from __future__ import annotations

from dataclasses import dataclass

import fakeredis.aioredis

from app.services.events import EventBus


@dataclass
class FakeRedisService:
    runtime: fakeredis.aioredis.FakeRedis


async def test_event_bus_publishes_and_replays_structured_events() -> None:
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bus = EventBus(FakeRedisService(runtime=fake_redis), prefix="hb:test")  # type: ignore[arg-type]

    event_id = await bus.publish(
        "run-1",
        "agent.started",
        {"query": "渠道防渗"},
        agent="research_agent",
        step="search",
    )
    events = await bus.read("run-1", "0-0", block_ms=1)

    assert event_id
    assert len(events) == 1
    assert events[0][1]["event_type"] == "agent.started"
    assert events[0][1]["data"] == {"query": "渠道防渗"}
    await fake_redis.aclose()
