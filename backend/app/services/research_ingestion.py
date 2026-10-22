from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.domain import Achievement, AchievementSource
from app.db.models.knowledge import OutboxEvent
from app.services.research_ranking import canonicalize_url


def normalize_achievement_title(title: str) -> str:
    return re.sub(r"[\s《》“”\"'（）()]+", "", title).casefold()


@dataclass(frozen=True)
class IngestionThresholds:
    review: float = 60.0
    auto_approve: float = 80.0
    review_limit: int = 5


class ResearchIngestionService:
    """Score external findings, route the grey zone, and persist approved results."""

    def __init__(self) -> None:
        self.thresholds = IngestionThresholds(
            review=settings.research_ingestion_review_threshold,
            auto_approve=settings.research_ingestion_auto_threshold,
            review_limit=settings.research_ingestion_review_limit,
        )

    @staticmethod
    def _completeness(candidate: dict[str, Any]) -> float:
        weighted_fields = (
            ("title", 0.15),
            ("abstract", 0.20),
            ("organizations", 0.15),
            ("technical_principle", 0.15),
            ("application_scenarios", 0.10),
            ("metrics", 0.10),
            ("maturity_level", 0.05),
            ("source_urls", 0.10),
        )
        return sum(weight for field, weight in weighted_fields if candidate.get(field))

    @staticmethod
    def _traceability(candidate: dict[str, Any]) -> float:
        evidence = list(candidate.get("evidence", []))
        if not evidence:
            return 0.0
        direct = sum(item.get("support_type") == "direct" for item in evidence) / len(evidence)
        quoted = sum(bool(item.get("evidence_text")) for item in evidence) / len(evidence)
        source_bound = sum(bool(item.get("source_url")) for item in evidence) / len(evidence)
        curator_score = max(0.0, min(1.0, float(candidate.get("evidence_score", 0))))
        return 0.35 * direct + 0.20 * quoted + 0.20 * source_bound + 0.25 * curator_score

    @staticmethod
    def _source_lookup(search_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            canonicalize_url(str(item.get("url", ""))): item
            for item in search_results
            if item.get("url")
        }

    def score_candidates(
        self,
        evidence_records: list[dict[str, Any]],
        search_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_lookup = self._source_lookup(search_results)
        scored: list[dict[str, Any]] = []
        for index, record in enumerate(evidence_records):
            candidate = dict(record)
            candidate_id = hashlib.sha256(
                f"{candidate.get('normalized_title') or candidate.get('title')}:{index}".encode()
            ).hexdigest()[:16]
            source_items = [
                source_lookup[canonicalize_url(str(url))]
                for url in candidate.get("source_urls", [])
                if canonicalize_url(str(url)) in source_lookup
            ]

            relevance = max(
                (float(item.get("relevance_score", 0)) for item in source_items),
                default=0.0,
            )
            authority = max(
                (float(item.get("authority_score", 0)) for item in source_items),
                default=0.0,
            )
            completeness = self._completeness(candidate)
            traceability = self._traceability(candidate)
            temporal_statuses = {
                str(item.get("temporal_status", "not_requested")) for item in source_items
            }
            if "in_range" in temporal_statuses or "not_requested" in temporal_statuses:
                temporal = 1.0
            elif "unknown" in temporal_statuses:
                temporal = 0.35
            else:
                temporal = 0.0
            source_count = len(set(candidate.get("source_urls", [])))
            query_coverage = max(
                (float(item.get("query_coverage", 0)) for item in source_items),
                default=0.0,
            )
            consistency = min(1.0, 0.35 + 0.25 * source_count + 0.40 * query_coverage)
            components = {
                "relevance": round(relevance * 20, 2),
                "authority": round(authority * 20, 2),
                "completeness": round(completeness * 20, 2),
                "traceability": round(traceability * 15, 2),
                "temporal": round(temporal * 10, 2),
                "consistency": round(consistency * 15, 2),
            }
            confidence = round(sum(components.values()), 2)

            hard_reasons: list[str] = []
            if not candidate.get("title") or not candidate.get("source_urls"):
                hard_reasons.append("缺少成果名称或可追溯来源")
            if not candidate.get("evidence"):
                hard_reasons.append("没有字段级原文证据")
            if source_items and all(
                item.get("temporal_status") == "out_of_range" for item in source_items
            ):
                hard_reasons.append("来源明确不满足时间约束")
            if source_items and all(
                float(item.get("focus_coverage", 0)) == 0 for item in source_items
            ):
                hard_reasons.append("来源未覆盖核心技术主题")
            if source_items and all(
                item.get("low_evidence_signals")
                or (
                    item.get("marketing_signals")
                    and item.get("authority_level") == "general_web"
                )
                for item in source_items
            ):
                hard_reasons.append("来源用途不匹配或属于营销内容")

            review_reasons: list[str] = []
            if any(
                item.get("support_type") == "conflict"
                for item in candidate.get("evidence", [])
            ):
                review_reasons.append("不同证据之间存在冲突")
            if not candidate.get("organizations"):
                review_reasons.append("完成单位或作者信息缺失")
            if source_items and all(
                item.get("authority_level") == "general_web" for item in source_items
            ):
                review_reasons.append("仅有普通网页来源")
            if "unknown" in temporal_statuses and "in_range" not in temporal_statuses:
                review_reasons.append("发布时间无法完全核实")
            if len(candidate.get("source_urls", [])) == 1 and candidate.get("metrics"):
                review_reasons.append("量化指标只有单一来源支持")

            if hard_reasons or confidence < self.thresholds.review:
                route = "rejected"
            elif review_reasons or confidence < self.thresholds.auto_approve:
                route = "human_review"
            else:
                route = "auto_approved"
            scored.append(
                {
                    **candidate,
                    "candidate_id": candidate_id,
                    "ingestion_confidence": confidence,
                    "score_components": components,
                    "route": route,
                    "hard_rejection_reasons": hard_reasons,
                    "review_reasons": review_reasons,
                    "source_metadata": [
                        {
                            "url": item.get("url", ""),
                            "title": item.get("title", ""),
                            "source_type": item.get("source_type", "web_source"),
                            "authority_level": item.get("authority_level", "unknown"),
                            "authority_score": item.get("authority_score", 0),
                            "temporal_status": item.get("temporal_status", "not_requested"),
                        }
                        for item in source_items
                    ],
                }
            )

        review_candidates = sorted(
            (item for item in scored if item["route"] == "human_review"),
            key=lambda item: float(item["ingestion_confidence"]),
            reverse=True,
        )
        allowed_review_ids = {
            item["candidate_id"] for item in review_candidates[: self.thresholds.review_limit]
        }
        for item in scored:
            if item["route"] == "human_review" and item["candidate_id"] not in allowed_review_ids:
                item["route"] = "deferred"
                item["review_reasons"].append("超过本次批量人工审核上限，保留为待处理线索")
        return scored

    async def persist_approved(
        self,
        session: AsyncSession,
        candidates: list[dict[str, Any]],
        *,
        organization_id: str | None,
        run_id: str,
        approval_mode: str,
        reviewer_id: str | None = None,
    ) -> list[str]:
        achievement_ids: list[str] = []
        for candidate in candidates:
            normalized_title = normalize_achievement_title(str(candidate.get("title", "")))
            if not normalized_title:
                continue
            existing = (
                await session.execute(
                    select(Achievement).where(
                        Achievement.organization_id == organization_id,
                        Achievement.normalized_title == normalized_title,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                achievement = Achievement(
                    organization_id=organization_id,
                    title=str(candidate.get("title", ""))[:500],
                    normalized_title=normalized_title,
                    abstract=str(candidate.get("abstract", "")),
                    technical_principle=str(candidate.get("technical_principle", "")),
                    application_scenarios=list(candidate.get("application_scenarios", [])),
                    organizations=list(candidate.get("organizations", [])),
                    maturity_level=candidate.get("maturity_level"),
                    advantages=list(candidate.get("advantages", [])),
                    limitations=list(candidate.get("limitations", [])),
                    metrics=list(candidate.get("metrics", [])),
                    tags=["联网成果", "Agent 自动发现"],
                    structured_data={
                        "source": "research_ingestion",
                        "agent_run_id": run_id,
                        "approval_mode": approval_mode,
                        "ingestion_confidence": candidate.get("ingestion_confidence", 0),
                        "score_components": candidate.get("score_components", {}),
                        "review_reasons": candidate.get("review_reasons", []),
                    },
                    evidence_score=float(candidate.get("ingestion_confidence", 0)) / 100,
                    status="approved",
                    approved_by=reviewer_id,
                    search_indexed=False,
                )
                session.add(achievement)
                await session.flush()
            else:
                achievement = existing
                if achievement.status != "approved":
                    achievement.status = "approved"
                    achievement.approved_by = reviewer_id
                    achievement.version += 1
                    achievement.search_indexed = False

            for url in candidate.get("source_urls", []):
                canonical_url = canonicalize_url(str(url))
                url_hash = hashlib.sha256(canonical_url.encode()).hexdigest()
                source_exists = await session.scalar(
                    select(AchievementSource.id).where(
                        AchievementSource.achievement_id == achievement.id,
                        AchievementSource.canonical_url_hash == url_hash,
                    )
                )
                if source_exists:
                    continue
                metadata = next(
                    (
                        item
                        for item in candidate.get("source_metadata", [])
                        if canonicalize_url(str(item.get("url", ""))) == canonical_url
                    ),
                    {},
                )
                session.add(
                    AchievementSource(
                        achievement_id=achievement.id,
                        source_type=str(metadata.get("source_type", "web"))[:50],
                        title=str(metadata.get("title", candidate.get("title", "")))[:500],
                        url=str(url),
                        canonical_url_hash=url_hash,
                        authority_level=str(metadata.get("authority_level", "unknown"))[:30],
                        is_primary_source=metadata.get("source_type")
                        in {"patent", "research_paper", "official_result_catalog"},
                        credibility_score=float(metadata.get("authority_score", 0)),
                        evidence=list(candidate.get("evidence", [])),
                    )
                )

            event_exists = await session.scalar(
                select(OutboxEvent.id).where(
                    OutboxEvent.aggregate_type == "achievement",
                    OutboxEvent.aggregate_id == achievement.id,
                    OutboxEvent.event_type == "achievement.approved",
                    OutboxEvent.version == achievement.version,
                )
            )
            if not event_exists and not achievement.search_indexed:
                session.add(
                    OutboxEvent(
                        aggregate_type="achievement",
                        aggregate_id=achievement.id,
                        event_type="achievement.approved",
                        version=achievement.version,
                        payload={
                            "achievement_id": achievement.id,
                            "version": achievement.version,
                            "agent_run_id": run_id,
                            "approval_mode": approval_mode,
                        },
                    )
                )
            achievement_ids.append(achievement.id)
        await session.commit()
        return achievement_ids
