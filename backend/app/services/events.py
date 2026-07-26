from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.services.redis_service import RedisService


class EventBus:
    def __init__(self, redis_service: RedisService, prefix: str = "hb:development") -> None:
        self.redis = redis_service.runtime
        self.prefix = prefix

    def stream_key(self, run_id: str) -> str:
        return f"{self.prefix}:run:{run_id}:events"

    async def publish(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        agent: str | None = None,
        step: str | None = None,
    ) -> str:
        payload = {
            "event_type": event_type,
            "run_id": run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "agent": agent or "",
            "step": step or "",
            "data": json.dumps(data or {}, ensure_ascii=False),
        }
        event_id = await self.redis.xadd(
            self.stream_key(run_id),
            payload,
            maxlen=10_000,
            approximate=True,
        )
        return str(event_id)

    async def read(
        self,
        run_id: str,
        last_event_id: str = "0-0",
        block_ms: int = 15_000,
    ) -> list[tuple[str, dict[str, Any]]]:
        result = await self.redis.xread(
            {self.stream_key(run_id): last_event_id},
            count=100,
            block=block_ms,
        )
        events: list[tuple[str, dict[str, Any]]] = []
        for _, entries in result:
            for event_id, fields in entries:
                data = dict(fields)
                raw_data = data.get("data", "{}")
                try:
                    data["data"] = json.loads(raw_data)
                except json.JSONDecodeError:
                    data["data"] = {"raw": raw_data}
                events.append((str(event_id), data))
        return events

    async def stream(
        self,
        run_id: str,
        last_event_id: str,
        is_terminal: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[str]:
        cursor = last_event_id or "0-0"
        idle_after_terminal = 0
        while True:
            events = await self.read(run_id, cursor)
            if events:
                idle_after_terminal = 0
                for event_id, event in events:
                    cursor = event_id
                    event["event_id"] = event_id
                    yield (
                        f"id: {event_id}\n"
                        f"event: {event['event_type']}\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
            elif await is_terminal():
                idle_after_terminal += 1
                if idle_after_terminal >= 1:
                    break
            else:
                yield ": keep-alive\n\n"
