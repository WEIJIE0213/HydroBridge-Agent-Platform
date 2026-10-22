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
from app.services.search_index import SearchIndexService

if TYPE_CHECKING:
    from app.services.agent_runtime import AgentRuntime


@dataclass
class ServiceContainer:
    redis: RedisService
    events: EventBus
    search: TavilySearchService
    search_index: SearchIndexService
    retrieval: RetrievalService
    object_storage: ObjectStorageService
    trace: RunTraceService
    agent_runtime: AgentRuntime | None = None

    @classmethod
    def create(cls) -> ServiceContainer:
        redis_service = RedisService()
        prefix = f"hb:{settings.app_env}"
        search_index = SearchIndexService()
        container = cls(
            redis=redis_service,
            events=EventBus(redis_service, prefix=prefix),
            search=TavilySearchService(redis_service),
            search_index=search_index,
            retrieval=RetrievalService(search_index),
            object_storage=ObjectStorageService(),
            trace=RunTraceService(),
        )
        from app.services.agent_runtime import AgentRuntime

        container.agent_runtime = AgentRuntime(container)
        return container

    async def close(self) -> None:
        await self.search.close()
        await self.search_index.close()
        await self.redis.close()
