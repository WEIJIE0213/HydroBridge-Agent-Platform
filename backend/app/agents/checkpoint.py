from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.engine import make_url

from app.core.config import settings

# Test/local-preview executions can pause and resume in separate HTTP calls.
# Reusing one in-memory saver preserves the interrupted State between them.
_test_checkpointer = InMemorySaver()


@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[object]:
    if settings.app_env == "test":
        yield _test_checkpointer
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    url = make_url(settings.database_url).set(drivername="postgresql")
    async with AsyncPostgresSaver.from_conn_string(
        url.render_as_string(hide_password=False)
    ) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
