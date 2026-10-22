from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any, ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.embeddings import get_embedding_provider, tokenize

TRACKING_QUERY_KEYS = {
    "from",
    "ref",
    "source",
    "spm",
    "tracking",
    "yclid",
    "_ga",
    "gclid",
    "fbclid",
}


def canonicalize_url(url: str) -> str:
    """Normalize a public URL so tracking variants do not become extra evidence."""

    parsed = urlsplit(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.casefold().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def requested_date_range(text: str) -> tuple[str | None, str | None, int | None]:
    """Extract explicit Chinese year constraints for Tavily and post-ranking."""

    range_match = re.search(
        r"(20\d{2})\s*年?\s*(?:至|到|[-—~～])\s*(20\d{2})\s*年?",
        text,
    )
    if range_match:
        start_year, end_year = sorted((int(range_match.group(1)), int(range_match.group(2))))
        return f"{start_year}-01-01", f"{end_year}-12-31", start_year
    since_match = re.search(r"(20\d{2})\s*年?以来", text)
    if since_match:
        year = int(since_match.group(1))
        return f"{year}-01-01", None, year
    return None, None, None


class ResearchRankingService:
    """Fuse multi-query web search results and select relevant, diverse evidence."""

    focus_terms: tuple[str, ...] = (
        "泵站",
        "水泵",
        "预测性维护",
        "预防性维护",
        "智能调度",
        "数字孪生",
        "农村饮水",
        "城乡供水",
        "供水",
        "水库",
        "大坝",
        "水闸",
        "灌溉",
        "灌区",
        "防洪",
        "防渗",
        "河道",
        "水生态",
        "污水处理",
        "水处理",
        "节水",
    )
    focus_aliases: ClassVar[dict[str, tuple[str, ...]]] = {
        "泵站": ("pumping station", "pump station"),
        "水泵": ("water pump", "pump"),
        "预测性维护": ("predictive maintenance", "condition-based maintenance"),
        "预防性维护": ("preventive maintenance",),
        "智能调度": (
            "intelligent scheduling",
            "smart scheduling",
            "optimal scheduling",
            "scheduling optimization",
        ),
        "数字孪生": ("digital twin",),
        "农村饮水": ("rural drinking water",),
        "城乡供水": ("urban-rural water supply",),
        "供水": ("water supply",),
        "水库": ("reservoir",),
        "大坝": ("dam",),
        "水闸": ("sluice", "floodgate"),
        "灌溉": ("irrigation",),
        "灌区": ("irrigation district",),
        "防洪": ("flood control",),
        "污水处理": ("wastewater treatment",),
        "水处理": ("water treatment",),
        "节水": ("water saving", "water conservation"),
    }
    trusted_domains: frozenset[str] = frozenset(
        {
            "iwra.org",
            "worldbank.org",
            "unesco.org",
            "aas.net.cn",
            "ccn.ac.cn",
        }
    )
    marketing_terms: tuple[str, ...] = (
        "解决方案",
        "产品方案",
        "厂家",
        "供应商",
        "报价",
        "咨询热线",
        "联系我们",
        "专家诊断",
        "官网",
        "无代码",
        "企业级",
        "定制",
        "request a demo",
        "book a demo",
        "contact sales",
        "get started",
        "pricing",
        "free trial",
        "asset management software",
    )
    low_evidence_terms: tuple[str, ...] = (
        "年度报告",
        "半年度报告",
        "学位授予标准",
        "培养方案",
        "招生简章",
        "招生目录",
        "招聘公告",
        "招标公告",
        "采购公告",
        "会议通知",
        "会议成功举办",
        "市场规模",
        "工作要点",
        "周年专辑",
        "週年專輯",
        "年鉴",
        "年鑑",
    )
    rrf_k = 60

    @classmethod
    def _source_type(cls, title: str, url: str) -> tuple[str, float]:
        """Classify evidence purpose for ranking and later explanation."""

        lowered_url = url.casefold()
        lowered_title = title.casefold()
        if "patents.google." in lowered_url or "专利" in title:
            return "patent", 1.08
        if any(term in title for term in ("成果推广清单", "科技成果登记", "获奖成果")):
            return "official_result_catalog", 1.07
        if any(term in title for term in ("工程应用", "示范工程", "试点项目", "验收")):
            return "engineering_case", 1.06
        if any(
            term in title for term in ("研究", "论文", "关键技术", "技术的应用")
        ) or any(
            term in lowered_title
            for term in ("research", "review", "study", "journal", "paper")
        ):
            return "research_paper", 1.04
        return "web_source", 1.0

    @classmethod
    def authority_score(cls, url: str) -> tuple[float, str]:
        domain = urlsplit(url).hostname or ""
        domain = domain.casefold().removeprefix("www.")
        if domain.endswith((".gov.cn", ".edu.cn", ".ac.cn")):
            return 1.0, "official_or_academic"
        if domain.endswith((".edu", ".ac.uk")) or any(
            domain == trusted or domain.endswith(f".{trusted}")
            for trusted in cls.trusted_domains
        ):
            return 0.88, "academic_or_international"
        if domain.endswith((".org", ".org.cn")):
            return 0.72, "industry_or_nonprofit"
        return 0.45, "general_web"

    @staticmethod
    def _aggregate(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        aggregated: dict[str, dict[str, Any]] = {}
        fallback_rank_by_query: dict[str, int] = {}
        for item in results:
            url = str(item.get("url", ""))
            if not url:
                continue
            canonical_url = canonicalize_url(url)
            query = str(item.get("query", ""))
            fallback_rank_by_query[query] = fallback_rank_by_query.get(query, 0) + 1
            query_rank = int(item.get("query_rank") or fallback_rank_by_query[query])
            existing = aggregated.get(canonical_url)
            if existing is None:
                aggregated[canonical_url] = {
                    **item,
                    "canonical_url": canonical_url,
                    "matched_queries": [query] if query else [],
                    "query_ranks": [query_rank],
                    "duplicate_count": 1,
                    "all_snippets": [str(item.get("snippet", ""))]
                    if item.get("snippet")
                    else [],
                }
                continue

            existing["duplicate_count"] = int(existing["duplicate_count"]) + 1
            snippet = str(item.get("snippet", ""))
            if snippet and snippet not in existing["all_snippets"]:
                existing["all_snippets"].append(snippet)
            if query and query not in existing["matched_queries"]:
                existing["matched_queries"].append(query)
                existing["query_ranks"].append(query_rank)
            if float(item.get("score", 0) or 0) > float(existing.get("score", 0) or 0):
                existing["title"] = item.get("title", existing.get("title", ""))
                existing["snippet"] = item.get("snippet", existing.get("snippet", ""))
                existing["score"] = item.get("score", existing.get("score", 0))
                existing["url"] = url
        return list(aggregated.values())

    @staticmethod
    def _evidence_passage(item: dict[str, Any]) -> str:
        snippets = item.get("all_snippets") or [str(item.get("snippet", ""))]
        return f"{item.get('title', '')}\n{' '.join(snippets)}"[:8_000]

    @staticmethod
    def _similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @classmethod
    def _diversify(
        cls,
        ranked: Sequence[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """MMR-style selection balancing relevance, novelty and domain diversity."""

        remaining = [dict(item) for item in ranked]
        selected: list[dict[str, Any]] = []
        selected_tokens: list[set[str]] = []
        selected_domains: set[str] = set()
        while remaining and len(selected) < limit:
            best_index = 0
            best_selection_score = float("-inf")
            best_redundancy = 0.0
            for index, item in enumerate(remaining):
                tokens = set(tokenize(f"{item.get('title', '')} {item.get('snippet', '')}"))
                redundancy = max(
                    (cls._similarity(tokens, prior) for prior in selected_tokens),
                    default=0.0,
                )
                domain = str(item.get("source_domain", ""))
                domain_novelty = 0.0 if domain in selected_domains else 1.0
                if not selected:
                    selection_score = float(item["ranking_score"])
                else:
                    selection_score = (
                        0.88 * float(item["ranking_score"])
                        + 0.08 * (1 - redundancy)
                        + 0.04 * domain_novelty
                    )
                if selection_score > best_selection_score:
                    best_index = index
                    best_selection_score = selection_score
                    best_redundancy = redundancy
            chosen = remaining.pop(best_index)
            chosen["selection_score"] = round(best_selection_score, 4)
            chosen["diversity_penalty"] = round(best_redundancy, 4)
            selected.append(chosen)
            selected_tokens.append(
                set(tokenize(f"{chosen.get('title', '')} {chosen.get('snippet', '')}"))
            )
            selected_domains.add(str(chosen.get("source_domain", "")))
        return selected

    @classmethod
    async def rank(
        cls,
        results: Sequence[dict[str, Any]],
        user_request: str,
        *,
        query_count: int | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        if not results:
            return []
        aggregated = cls._aggregate(results)
        passages = [cls._evidence_passage(item) for item in aggregated]
        provider = get_embedding_provider()
        relevance_scores = await asyncio.to_thread(provider.rerank, user_request, passages)
        active_focus = [term for term in cls.focus_terms if term in user_request]
        _, _, minimum_year = requested_date_range(user_request)
        inferred_query_count = len(
            {str(item.get("query", "")) for item in results if item.get("query")}
        )
        total_queries = max(1, query_count or inferred_query_count or 1)
        rrf_max = total_queries / (cls.rrf_k + 1)

        ranked: list[dict[str, Any]] = []
        for item, relevance in zip(aggregated, relevance_scores, strict=True):
            passage = cls._evidence_passage(item)
            lowered_passage = passage.casefold()

            def covers_focus(term: str, target: str) -> bool:
                aliases = (term, *cls.focus_aliases.get(term, ()))
                return any(alias.casefold() in target for alias in aliases)

            matched_focus = [
                term for term in active_focus if covers_focus(term, lowered_passage)
            ]
            title = str(item.get("title", ""))
            lowered_title = title.casefold()
            title_matched_focus = [
                term for term in active_focus if covers_focus(term, lowered_title)
            ]
            if active_focus and not matched_focus and float(relevance) < 0.18:
                continue
            authority, authority_level = cls.authority_score(str(item.get("url", "")))
            tavily_score = max(0.0, min(1.0, float(item.get("score", 0) or 0)))
            query_ranks = [max(1, int(rank)) for rank in item.get("query_ranks", [1])]
            rrf_raw = sum(1 / (cls.rrf_k + rank) for rank in query_ranks)
            rrf_score = min(1.0, rrf_raw / rrf_max)
            query_coverage = min(
                1.0,
                len(item.get("matched_queries", [])) / total_queries,
            )
            focus_coverage = (
                len(matched_focus) / len(active_focus) if active_focus else 1.0
            )
            title_focus_coverage = (
                len(title_matched_focus) / len(active_focus) if active_focus else 1.0
            )
            specificity_penalty = (
                0.82 if len(active_focus) >= 2 and not title_matched_focus else 1.0
            )
            marketing_signals = [
                term
                for term in cls.marketing_terms
                if term.casefold() in lowered_passage
            ]
            marketing_penalty = (
                0.78 if authority_level == "general_web" and marketing_signals else 1.0
            )
            low_evidence_signals = [
                term for term in cls.low_evidence_terms if term in passage
            ]
            is_event_news = any(
                event_term in title for event_term in ("会议", "交流会", "论坛")
            ) and any(term in title for term in ("成功举办", "召开", "开幕"))
            if is_event_news:
                low_evidence_signals.append("会议新闻")
            parsed_url = urlsplit(str(item.get("url", "")))
            if parsed_url.path.rstrip("/") == "":
                low_evidence_signals.append("站点首页")
            # These pages may mention the requested technology, but their primary
            # purpose is neither describing a result nor proving an application.
            evidence_purpose_penalty = 0.58 if low_evidence_signals else 1.0
            source_type, source_type_boost = cls._source_type(
                title,
                str(item.get("url", "")),
            )

            source_years = [int(value) for value in re.findall(r"20\d{2}", passage)]
            recency_score = 1.0
            temporal_status = "not_requested"
            temporal_penalty = 1.0
            if minimum_year:
                if not source_years:
                    recency_score = 0.65
                    temporal_status = "unknown"
                    temporal_penalty = 0.90
                elif max(source_years) < minimum_year:
                    recency_score = max(0.1, 1 - (minimum_year - max(source_years)) * 0.18)
                    temporal_status = "out_of_range"
                    temporal_penalty = 0.55
                else:
                    temporal_status = "in_range"

            ranking_score = (
                float(relevance) * 0.36
                + tavily_score * 0.15
                + authority * 0.22
                + rrf_score * 0.17
                + focus_coverage * 0.10
            )
            ranking_score *= temporal_penalty
            ranking_score *= marketing_penalty
            ranking_score *= specificity_penalty
            ranking_score *= evidence_purpose_penalty
            ranking_score *= source_type_boost
            domain = (urlsplit(str(item.get("url", ""))).hostname or "").casefold()
            ranked.append(
                {
                    **item,
                    "relevance_score": round(float(relevance), 4),
                    "authority_score": authority,
                    "authority_level": authority_level,
                    "matched_focus_terms": matched_focus,
                    "title_matched_focus_terms": title_matched_focus,
                    "focus_coverage": round(focus_coverage, 4),
                    "title_focus_coverage": round(title_focus_coverage, 4),
                    "specificity_penalty": specificity_penalty,
                    "query_coverage": round(query_coverage, 4),
                    "rrf_score": round(rrf_score, 4),
                    "recency_score": round(recency_score, 4),
                    "temporal_status": temporal_status,
                    "temporal_penalty": temporal_penalty,
                    "marketing_signals": marketing_signals,
                    "marketing_penalty": marketing_penalty,
                    "low_evidence_signals": low_evidence_signals,
                    "evidence_purpose_penalty": evidence_purpose_penalty,
                    "source_type": source_type,
                    "source_type_boost": source_type_boost,
                    "source_domain": domain.removeprefix("www."),
                    "ranking_score": round(ranking_score, 4),
                }
            )
        ranked.sort(
            key=lambda item: (
                float(item["ranking_score"]),
                float(item["authority_score"]),
                float(item.get("score", 0) or 0),
            ),
            reverse=True,
        )
        return cls._diversify(ranked, limit)
