from __future__ import annotations

import pytest

from app.agents.specialists import ResearchAgent, accepted_search_results
from app.services.embeddings import HashEmbeddingProvider


async def test_research_ranking_rejects_off_topic_and_prefers_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agents.specialists.get_embedding_provider",
        lambda: HashEmbeddingProvider(dimension=256),
    )
    request = "调研2024年以来智慧泵站智能调度与预测性维护技术进展"
    results = [
        {
            "title": "智慧泵站智能调度与预测性维护试点",
            "url": "https://example.gov.cn/water/pump-2025",
            "snippet": "2025年水利工程智慧泵站开展智能调度和预测性维护试点。",
            "score": 0.72,
        },
        {
            "title": "智慧泵站智能调度与预测性维护方案",
            "url": "https://vendor.example.com/pump",
            "snippet": "2025年智慧泵站智能调度和预测性维护产品方案。",
            "score": 0.72,
        },
        {
            "title": "含盐污水处理技术手册",
            "url": "https://example.edu.cn/wastewater",
            "snippet": "2025年含盐污水生物处理技术，降低污泥和能耗。",
            "score": 0.99,
        },
    ]

    ranked = await ResearchAgent._rank_results(results, request)

    assert [item["title"] for item in ranked] == [
        "智慧泵站智能调度与预测性维护试点",
        "智慧泵站智能调度与预测性维护方案",
    ]
    assert ranked[0]["authority_level"] == "official_or_academic"
    assert ranked[0]["ranking_score"] > ranked[1]["ranking_score"]


def test_final_citations_only_include_curator_accepted_sources() -> None:
    state = {
        "search_results": [
            {"title": "政府试点", "url": "https://example.gov.cn/pump"},
            {"title": "市场规模", "url": "https://market.example.com/report"},
        ],
        "evidence_records": [
            {"source_urls": ["https://example.gov.cn/pump"]},
        ],
    }

    assert accepted_search_results(state) == [state["search_results"][0]]
