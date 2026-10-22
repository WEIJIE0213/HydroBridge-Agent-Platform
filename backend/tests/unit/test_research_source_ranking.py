from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.specialists import (
    EvidenceCuratorAgent,
    ResearchAgent,
    accepted_search_results,
    focused_excerpt,
)
from app.services.embeddings import HashEmbeddingProvider
from app.services.research_ranking import (
    ResearchRankingService,
    canonicalize_url,
    requested_date_range,
)


async def test_research_ranking_rejects_off_topic_and_prefers_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.research_ranking.get_embedding_provider",
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


def test_url_canonicalization_removes_tracking_variants() -> None:
    first = "https://www.example.gov.cn/water/result/?utm_source=test&id=7#summary"
    second = "https://example.gov.cn/water/result?id=7"

    assert canonicalize_url(first) == canonicalize_url(second)


def test_fallback_query_core_keeps_terms_but_drops_long_instructions() -> None:
    request = (
        "调研2024年以来智慧泵站智能调度与预测性维护相关科技成果和工程应用，"
        "优先政府、高校、科研院所和原始论文，说明关键指标、应用案例与局限。"
    )

    core = ResearchAgent._fallback_query_core(request)

    assert core == "2024年以来 泵站 预测性维护 智能调度"
    assert "优先政府" not in core


@pytest.mark.parametrize(
    ("user_request", "expected"),
    [
        ("调研2024年以来智慧泵站技术", ("2024-01-01", None, 2024)),
        ("调研2022年至2025年水库监测技术", ("2022-01-01", "2025-12-31", 2022)),
        ("调研智慧灌区技术", (None, None, None)),
    ],
)
def test_requested_date_range_is_available_to_tavily(
    user_request: str,
    expected: tuple[str | None, str | None, int | None],
) -> None:
    assert requested_date_range(user_request) == expected


async def test_multi_query_rrf_merges_url_variants_and_rewards_consensus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.research_ranking.get_embedding_provider",
        lambda: HashEmbeddingProvider(dimension=256),
    )
    request = "智慧泵站智能调度与预测性维护技术"
    results = [
        {
            "query": "智慧泵站技术成果",
            "query_rank": 1,
            "title": "智慧泵站智能调度与预测性维护试点",
            "url": "https://example.gov.cn/pump?id=7&utm_source=a",
            "snippet": "智慧泵站智能调度与预测性维护工程试点。",
            "score": 0.68,
        },
        {
            "query": "泵站预测性维护工程应用",
            "query_rank": 2,
            "title": "智慧泵站智能调度与预测性维护试点",
            "url": "https://www.example.gov.cn/pump?id=7#case",
            "snippet": "智慧泵站预测性维护技术已完成工程示范。",
            "score": 0.66,
        },
        {
            "query": "智慧泵站技术成果",
            "query_rank": 1,
            "title": "智慧泵站预测性维护产品方案",
            "url": "https://vendor.example.com/product",
            "snippet": "智慧泵站智能调度和预测性维护产品宣传。",
            "score": 0.95,
        },
        {
            "query": "泵站预测性维护工程应用",
            "query_rank": 1,
            "title": "污水处理市场规模报告",
            "url": "https://irrelevant.example.edu.cn/report",
            "snippet": "污水处理市场规模和投资预测。",
            "score": 0.99,
        },
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=2)

    assert ranked[0]["title"] == "智慧泵站智能调度与预测性维护试点"
    assert ranked[0]["duplicate_count"] == 2
    assert ranked[0]["query_coverage"] == 1
    assert len([item for item in ranked if "example.gov.cn/pump" in item["canonical_url"]]) == 1
    assert all(item["title"] != "污水处理市场规模报告" for item in ranked)


async def test_url_merge_preserves_date_evidence_from_every_query() -> None:
    request = "查找2024年以来泵站智能调度成果"
    results = [
        {
            "query": "泵站智能调度成果",
            "query_rank": 1,
            "title": "泵站智能调度技术的应用",
            "url": "https://journal.example.org/paper?utm_source=first",
            "snippet": "介绍泵站智能调度方法。",
            "score": 0.9,
        },
        {
            "query": "泵站工程应用",
            "query_rank": 2,
            "title": "泵站智能调度技术的应用",
            "url": "https://journal.example.org/paper",
            "snippet": "水电科技 2024 年第 7 期，给出泵站工程应用指标。",
            "score": 0.7,
        },
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=2)

    assert len(ranked) == 1
    assert ranked[0]["duplicate_count"] == 2
    assert len(ranked[0]["all_snippets"]) == 2
    assert ranked[0]["temporal_status"] == "in_range"


async def test_chinese_request_matches_english_research_terms() -> None:
    request = "查找2024年以来泵站预测性维护和智能调度成果"
    results = [
        {
            "query": "water pumping station predictive maintenance research",
            "query_rank": 1,
            "title": "Predictive Maintenance and Intelligent Scheduling for Pumping Stations",
            "url": "https://journal.example.org/2025/pump-research",
            "snippet": "Published in 2025 with a digital-twin pumping station case study.",
            "score": 0.8,
        }
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=1)

    assert ranked[0]["matched_focus_terms"] == ["泵站", "预测性维护", "智能调度"]
    assert ranked[0]["title_focus_coverage"] == 1
    assert ranked[0]["temporal_status"] == "in_range"


async def test_english_vendor_page_is_marked_as_marketing() -> None:
    request = "查找2024年以来泵站预测性维护成果"
    results = [
        {
            "query": "pumping station predictive maintenance research",
            "query_rank": 1,
            "title": "AI Predictive Maintenance for Municipal Water Pump Stations",
            "url": "https://vendor.example.com/pump-maintenance",
            "snippet": "Published in 2025. Request a demo and contact sales to get started.",
            "score": 0.9,
        }
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=1)

    assert ranked[0]["authority_level"] == "general_web"
    assert "request a demo" in ranked[0]["marketing_signals"]
    assert ranked[0]["marketing_penalty"] < 1


async def test_english_review_is_typed_as_research_not_generic_web() -> None:
    request = "查找2024年以来泵站预测性维护成果"
    results = [
        {
            "query": "pumping station predictive maintenance research",
            "query_rank": 1,
            "title": "A Comprehensive Review of Predictive Maintenance in Pumps",
            "url": "https://journal.example.com/index.php/tech/article/view/57",
            "snippet": "Published in 2025; reviews predictive maintenance for pumping stations.",
            "score": 0.8,
        }
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=1)

    assert ranked[0]["source_type"] == "research_paper"


def test_focused_excerpt_supports_english_aliases() -> None:
    text = "navigation " * 100 + "predictive maintenance reduced pump downtime by 30%."
    aliases = [
        alias
        for term in ["预测性维护"]
        for alias in (term, *ResearchRankingService.focus_aliases.get(term, ()))
    ]

    excerpt = focused_excerpt(text, aliases, limit=160)

    assert "predictive maintenance reduced pump downtime" in excerpt


async def test_mmr_selection_preserves_source_diversity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.research_ranking.get_embedding_provider",
        lambda: HashEmbeddingProvider(dimension=256),
    )
    request = "农村供水智能监测技术成果"
    results = [
        {
            "query": "农村供水智能监测",
            "query_rank": rank,
            "title": f"农村供水智能监测企业方案{rank}",
            "url": f"https://vendor.example.com/product/{rank}",
            "snippet": "农村供水智能监测平台产品方案和设备介绍。",
            "score": 0.92 - rank * 0.01,
        }
        for rank in range(1, 4)
    ]
    results.append(
        {
            "query": "农村供水智能监测",
            "query_rank": 4,
            "title": "农村供水智能监测试点",
            "url": "https://water.example.gov.cn/case",
            "snippet": "农村供水智能监测工程试点和验收信息。",
            "score": 0.72,
        }
    )

    ranked = await ResearchRankingService.rank(results, request, query_count=1, limit=3)

    assert len({item["source_domain"] for item in ranked}) >= 2
    assert any(item["authority_level"] == "official_or_academic" for item in ranked)


async def test_generic_landing_page_does_not_beat_specific_research_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.research_ranking.get_embedding_provider",
        lambda: HashEmbeddingProvider(dimension=256),
    )
    request = "智慧泵站智能调度与预测性维护技术"
    results = [
        {
            "query": "智慧泵站科研成果",
            "query_rank": 1,
            "title": "某高校科研动态",
            "url": "https://news.example.edu.cn/brief",
            "snippet": "介绍智慧泵站智能调度与预测性维护技术。",
            "score": 0.85,
        },
        {
            "query": "智慧泵站科研成果",
            "query_rank": 2,
            "title": "智慧泵站智能调度与预测性维护研究",
            "url": "https://journal.example.org/paper",
            "snippet": "研究智慧泵站智能调度与预测性维护方法。",
            "score": 0.72,
        },
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=1)

    assert ranked[0]["title"] == "智慧泵站智能调度与预测性维护研究"
    assert ranked[1]["specificity_penalty"] < 1


async def test_deterministic_curator_rejects_old_and_marketing_sources() -> None:
    state = {
        "search_results": [
            {
                "title": "2022年智能泵站试点",
                "url": "https://old.example.gov.cn/case",
                "snippet": "旧试点",
                "temporal_status": "out_of_range",
                "authority_level": "official_or_academic",
            },
            {
                "title": "智慧泵站解决方案",
                "url": "https://vendor.example.com/product",
                "snippet": "联系我们获取报价",
                "temporal_status": "unknown",
                "authority_level": "general_web",
                "marketing_signals": ["解决方案", "联系我们", "报价"],
            },
            {
                "title": "智能泵站预测性维护研究",
                "url": "https://journal.example.org/unknown-date",
                "snippet": "页面没有可核实的发表时间。",
                "temporal_status": "unknown",
                "authority_level": "industry_or_nonprofit",
            },
            {
                "title": "AI Predictive Maintenance for Pump Stations",
                "url": "https://vendor.example.com/landing-page",
                "snippet": "AI-based predictive maintenance published in 2025.",
                "content": "Technical introduction. Book a demo and contact sales today.",
                "temporal_status": "in_range",
                "authority_level": "general_web",
            },
            {
                "title": "某公司2025年年度报告",
                "url": "https://company.example.com/annual-report.pdf",
                "snippet": "报告提到智慧泵站智能调度业务。",
                "temporal_status": "in_range",
                "authority_level": "general_web",
                "low_evidence_signals": ["年度报告"],
            },
            {
                "title": "2025年度成熟适用水利科技成果推广清单",
                "url": "https://example.gov.cn/result-catalog",
                "snippet": "介绍水土保持监测监管技术。",
                "temporal_status": "in_range",
                "authority_level": "official_or_academic",
                "focus_coverage": 0,
            },
            {
                "title": "2025年智慧泵站工程研究",
                "url": "https://journal.example.org/paper",
                "snippet": "2025年智慧泵站工程研究与关键指标。",
                "temporal_status": "in_range",
                "authority_level": "industry_or_nonprofit",
            },
        ]
    }
    deps = SimpleNamespace(model=SimpleNamespace(configured=False))

    batch = await EvidenceCuratorAgent().curate(state, deps)  # type: ignore[arg-type]

    assert [item.title for item in batch.achievements] == ["2025年智慧泵站工程研究"]
    assert len(batch.rejected_sources) == 6


async def test_document_purpose_penalizes_annual_reports() -> None:
    request = "查找2024年以来泵站预测性维护和智能调度科技成果"
    results = [
        {
            "query": "泵站预测性维护成果",
            "query_rank": 1,
            "title": "某公司2025年年度报告",
            "url": "https://company.example.com/annual-report.pdf",
            "snippet": "2025年智慧泵站预测性维护和智能调度业务。",
            "score": 0.9,
        },
        {
            "query": "泵站预测性维护成果",
            "query_rank": 2,
            "title": "2025年泵站预测性维护关键技术与工程应用",
            "url": "https://journal.example.org/research",
            "snippet": "给出故障预测方法、关键指标与工程示范数据。",
            "score": 0.75,
        },
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=1)

    assert ranked[0]["url"] == "https://journal.example.org/research"
    annual_report = next(item for item in ranked if "annual-report" in item["url"])
    assert annual_report["low_evidence_signals"] == ["年度报告"]
    assert annual_report["evidence_purpose_penalty"] < 1


async def test_document_purpose_flags_event_news_and_homepage() -> None:
    request = "查找2024年以来泵站智能调度科技成果"
    results = [
        {
            "query": "泵站智能调度科技成果",
            "query_rank": 1,
            "title": "水利信息化技术交流会在宜昌成功举办",
            "url": "https://example.edu.cn/news/2026/meeting.html",
            "snippet": "会议报告提到泵站智能调度。",
            "score": 0.9,
        },
        {
            "query": "泵站智能调度科技成果",
            "query_rank": 2,
            "title": "水利水务网",
            "url": "https://example.org/",
            "snippet": "泵站、智能调度、科技成果导航。",
            "score": 0.85,
        },
        {
            "query": "泵站智能调度科技成果",
            "query_rank": 3,
            "title": "一种泵站智能调度方法及系统",
            "url": "https://patents.google.com/patent/CN123456A/zh",
            "snippet": "公开泵站优化调度模型、约束条件和求解方法。",
            "score": 0.72,
        },
    ]

    ranked = await ResearchRankingService.rank(results, request, query_count=1)

    patent = next(item for item in ranked if item["source_type"] == "patent")
    event = next(item for item in ranked if "meeting" in item["url"])
    homepage = next(item for item in ranked if item["url"] == "https://example.org/")
    assert ranked[0] == patent
    assert event["low_evidence_signals"] == ["会议新闻"]
    assert homepage["low_evidence_signals"] == ["站点首页"]


async def test_curator_backfills_after_rejecting_low_quality_candidates() -> None:
    state = {
        "search_results": [
            {
                "title": "水利会议成功举办",
                "url": "https://example.org/news",
                "snippet": "会议新闻",
                "low_evidence_signals": ["会议新闻"],
            },
            *[
                {
                    "title": f"成果 {index}",
                    "url": f"https://journal.example.org/paper-{index}",
                    "snippet": "泵站智能调度研究证据",
                }
                for index in range(1, 9)
            ],
        ]
    }
    deps = SimpleNamespace(model=SimpleNamespace(configured=False))

    batch = await EvidenceCuratorAgent().curate(state, deps)  # type: ignore[arg-type]

    assert len(batch.achievements) == 8
    assert all("会议" not in item.title for item in batch.achievements)


def test_focused_excerpt_centers_the_matching_evidence() -> None:
    text = "页面导航和通用介绍。" * 100 + "泵站预测性维护准确率达到92%。" + "页脚。" * 100

    excerpt = focused_excerpt(text, ["泵站", "预测性维护"], limit=180)

    assert "泵站预测性维护准确率达到92%" in excerpt
    assert len(excerpt) <= 182
