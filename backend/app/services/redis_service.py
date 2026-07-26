from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings


class RedisService:
    def __init__(
        self,
        runtime_url: str | None = None,
        cache_url: str | None = None,
    ) -> None:
        self.runtime = Redis.from_url(
            runtime_url or settings.redis_runtime_url,
            decode_responses=True,
            health_check_interval=30,
        )
        self.cache = Redis.from_url(
            cache_url or settings.redis_cache_url,
            decode_responses=True,
            health_check_interval=30,
        )

    async def health(self) -> dict[str, bool]:
        status = {"runtime": False, "cache": False}
        try:
            status["runtime"] = bool(await self.runtime.ping())
        except Exception:
            status["runtime"] = False
        try:
            status["cache"] = bool(await self.cache.ping())
        except Exception:
            status["cache"] = False
        return status

    async def close(self) -> None:
        await self.runtime.aclose()
        await self.cache.aclose()
