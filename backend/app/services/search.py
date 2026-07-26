from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.core.config import settings
from app.services.redis_service import RedisService


class SearchResult(BaseModel):
    title: str
    url: HttpUrl
    content: str = ""
    score: float = 0
    raw_content: str | None = None


class TavilySearchResponse(BaseModel):
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    response_time: float | None = None
    request_id: str | None = None


class TavilySearchService:
    base_url = "https://api.tavily.com"

    def __init__(self, redis_service: RedisService, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.tavily_api_key
        self.cache = redis_service.cache
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(45),
            follow_redirects=True,
            headers={"User-Agent": "HydroBridge-AI/1.0"},
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _cache_key(
        self,
        query: str,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> str:
        raw = json.dumps(
            {
                "query": query,
                "include_domains": sorted(include_domains or []),
                "exclude_domains": sorted(exclude_domains or []),
                "depth": settings.tavily_search_depth,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return (
            f"hb:{settings.app_env}:cache:search:"
            f"{hashlib.sha256(raw.encode()).hexdigest()}"
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential_jitter(initial=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        max_results: int | None = None,
        use_cache: bool = True,
    ) -> TavilySearchResponse:
        if not self.api_key:
            raise RuntimeError("Tavily is not configured")

        cache_key = self._cache_key(query, include_domains, exclude_domains)
        if use_cache:
            cached = await self.cache.get(cache_key)
            if cached:
                return TavilySearchResponse.model_validate_json(cached)

        payload: dict[str, Any] = {
            "query": query,
            "search_depth": settings.tavily_search_depth,
            "max_results": max_results or settings.tavily_max_results,
            "include_answer": False,
            "include_raw_content": False,
            "topic": "general",
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        response = await self.client.post(
            "/search",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        parsed = TavilySearchResponse.model_validate(response.json())
        if use_cache:
            await self.cache.set(cache_key, parsed.model_dump_json(), ex=1800)
        return parsed

    async def extract(self, urls: list[str], query: str | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Tavily is not configured")
        payload: dict[str, Any] = {
            "urls": urls[:20],
            "extract_depth": "advanced",
            "format": "markdown",
        }
        if query:
            payload["query"] = query
        response = await self.client.post(
            "/extract",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        return response.json()

    async def health(self) -> bool:
        return self.configured

    async def close(self) -> None:
        await self.client.aclose()
