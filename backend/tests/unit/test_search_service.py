from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx

from app.services.search import TavilySearchService


class FakeCache:
    async def get(self, key: str) -> None:
        return None

    async def set(self, key: str, value: str, ex: int) -> None:
        return None


def build_service() -> TavilySearchService:
    redis = SimpleNamespace(cache=FakeCache())
    return TavilySearchService(redis, api_key="test-key")  # type: ignore[arg-type]


def test_search_cache_key_includes_result_and_date_controls() -> None:
    service = build_service()
    common = {
        "query": "智慧泵站",
        "include_domains": None,
        "exclude_domains": None,
        "chunks_per_source": 3,
        "topic": "general",
        "time_range": None,
        "end_date": None,
    }

    first = service._cache_key(**common, max_results=6, start_date="2024-01-01")
    second = service._cache_key(**common, max_results=10, start_date="2024-01-01")
    third = service._cache_key(**common, max_results=6, start_date="2025-01-01")

    assert len({first, second, third}) == 3


@respx.mock
async def test_search_sends_targeted_chunks_and_explicit_date_filter() -> None:
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={"query": "智慧泵站", "results": []},
        )
    )
    service = build_service()
    try:
        await service.search(
            "智慧泵站",
            max_results=6,
            chunks_per_source=2,
            start_date="2024-01-01",
            use_cache=False,
        )
    finally:
        await service.close()

    payload = json.loads(route.calls[0].request.content)
    assert payload["search_depth"] == "advanced"
    assert payload["chunks_per_source"] == 2
    assert payload["start_date"] == "2024-01-01"
    assert payload["include_answer"] is False
    assert payload["include_raw_content"] is False


@respx.mock
async def test_extract_uses_query_focused_chunks() -> None:
    route = respx.post("https://api.tavily.com/extract").mock(
        return_value=httpx.Response(200, json={"results": [], "failed_results": []})
    )
    service = build_service()
    try:
        await service.extract(
            ["https://example.gov.cn/result"],
            query="泵站预测性维护",
            chunks_per_source=4,
        )
    finally:
        await service.close()

    payload = json.loads(route.calls[0].request.content)
    assert payload["extract_depth"] == "advanced"
    assert payload["query"] == "泵站预测性维护"
    assert payload["chunks_per_source"] == 4
    assert payload["timeout"] == 45
