from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import settings
from app.services.events import EventBus
from app.services.object_storage import ObjectStorageService
from app.services.redis_service import RedisService
from app.services.retrieval import RetrievalService
from app.services.run_trace import RunTraceService
from app.services.search import TavilySearchService
from app.services.vector_store import VectorStoreService

if TYPE_CHECKING:
    from app.services.agent_runtime import AgentRuntime


@dataclass
class ServiceContainer:
    redis: RedisService
    events: EventBus
    search: TavilySearchService
    vector_store: VectorStoreService
    retrieval: RetrievalService
    object_storage: ObjectStorageService
    trace: RunTraceService
    agent_runtime: AgentRuntime | None = None

    @classmethod
    def create(cls) -> ServiceContainer:
        redis_service = RedisService()
        prefix = f"hb:{settings.app_env}"
        vector_store = VectorStoreService()
        container = cls(
            redis=redis_service,
            events=EventBus(redis_service, prefix=prefix),
            search=TavilySearchService(redis_service),
            vector_store=vector_store,
            retrieval=RetrievalService(vector_store),
            object_storage=ObjectStorageService(),
            trace=RunTraceService(),
        )
        from app.services.agent_runtime import AgentRuntime

        container.agent_runtime = AgentRuntime(container)
        return container

    async def close(self) -> None:
        await self.search.close()
        await self.vector_store.close()
        await self.redis.close()
