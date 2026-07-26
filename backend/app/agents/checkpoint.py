from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import settings


@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[object]:
    if settings.app_env == "test":
        yield InMemorySaver()
        return

    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    async with AsyncRedisSaver.from_conn_string(settings.redis_runtime_url) as checkpointer:
        await checkpointer.asetup()
        yield checkpointer
