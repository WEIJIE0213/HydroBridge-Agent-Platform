from __future__ import annotations

import asyncio
import math
from collections import Counter, defaultdict
from typing import Any

from elasticsearch.helpers import async_bulk
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.domain import Achievement, AchievementSource
from app.db.models.knowledge import AchievementChunk
from app.services.chunking import FIELDS, chunk_achievement, field_text
from app.services.embeddings import get_embedding_provider, tokenize
from app.services.search_index import SearchIndexService

logger = get_logger("retrieval")


class RetrievalHit(BaseModel):
    achievement_id: str
    title: str
    abstract: str = ""
    score: float = 0
    reranker_score: float = 0
    payload: dict[str, Any] = Field(default_factory=dict)


def reciprocal_rank_fusion(rankings: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = defaultdict(float)
    documents = {}
    for ranking in rankings:
        seen = set()
        for rank, item in enumerate(ranking, 1):
            key = item["chunk_id"]
            if key not in seen:
                scores[key] += 1 / (k + rank)
                documents[key] = item
                seen.add(key)
    return [
        dict(documents[key], score=score)
        for key, score in sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    ]


def visibility_filter(organization_id: str | None):
    if organization_id:
        return or_(
            Achievement.organization_id.is_(None), Achievement.organization_id == organization_id
        )
    return Achievement.organization_id.is_(None)


class RetrievalService:
    def __init__(self, search_index: SearchIndexService) -> None:
        self.search_index = search_index

    @staticmethod
    def achievement_text(achievement: Achievement) -> str:
        return "\n".join(field_text(getattr(achievement, key, None)) for key in FIELDS)

    async def index_achievement(self, achievement: Achievement, session: AsyncSession) -> None:
        chunks = chunk_achievement(achievement, settings.chunk_size, settings.chunk_overlap)
        source_rows = list(
            (
                await session.scalars(
                    select(AchievementSource).where(
                        AchievementSource.achievement_id == achievement.id
                    )
                )
            ).all()
        )
        # Deterministic IDs make replay safe. The worker locks the aggregate until
        # PG commits so an old event cannot overwrite a newer version.
        await session.execute(
            delete(AchievementChunk).where(AchievementChunk.achievement_id == achievement.id)
        )
        for chunk in chunks:
            session.add(
                AchievementChunk(
                    id=chunk.id,
                    achievement_id=achievement.id,
                    version=achievement.version,
                    ordinal=chunk.ordinal,
                    field=chunk.field,
                    content=chunk.text,
                    start_offset=chunk.start,
                    end_offset=chunk.end,
                    content_hash=chunk.content_hash,
                    source_ids=[
                        s.id
                        for s in source_rows
                        if (chunk.document_id is None or s.document_id == chunk.document_id)
                    ],
                )
            )
        await session.flush()
        if settings.retrieval_backend == "local":
            return
        await self.search_index.ensure_index()
        provider = get_embedding_provider()
        actions = []
        vectors = await asyncio.to_thread(
            provider.embed_many, [f"{achievement.title}\n{chunk.text}" for chunk in chunks]
        )
        for chunk, vector in zip(chunks, vectors, strict=True):
            if len(vector) != settings.embedding_dimension or not any(vector):
                raise ValueError("Embedding dimension mismatch or zero vector")
            actions.append(
                {
                    "_index": settings.elasticsearch_index,
                    "_id": chunk.id,
                    "_source": {
                        "chunk_id": chunk.id,
                        "achievement_id": achievement.id,
                        "version": achievement.version,
                        "ordinal": chunk.ordinal,
                        "field": chunk.field,
                        "text": chunk.text,
                        "title": achievement.title,
                        "content_hash": chunk.content_hash,
                        "organization_id": achievement.organization_id or "",
                        "taxonomy_id": achievement.taxonomy_id or "",
                        "status": achievement.status,
                        "maturity_level": achievement.maturity_level,
                        "embedding": vector,
                        "embedding_model": provider.model_name,
                    },
                }
            )
        await async_bulk(self.search_index.client, actions, refresh="wait_for")
        await self.search_index.client.delete_by_query(
            index=settings.elasticsearch_index,
            refresh=True,
            query={
                "bool": {
                    "filter": [{"term": {"achievement_id": achievement.id}}],
                    "must_not": [{"term": {"version": achievement.version}}],
                }
            },
        )

    async def delete_achievement(self, achievement_id: str, session: AsyncSession) -> None:
        await session.execute(
            delete(AchievementChunk).where(AchievementChunk.achievement_id == achievement_id)
        )
        if (
            settings.retrieval_backend == "elasticsearch"
            and await self.search_index.client.indices.exists(index=settings.elasticsearch_index)
        ):
            await self.search_index.client.delete_by_query(
                index=settings.elasticsearch_index,
                query={"term": {"achievement_id": achievement_id}},
                refresh=True,
            )

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        organization_id: str | None = None,
        limit: int = 10,
        constraints: dict[str, Any] | None = None,
        achievement_ids: list[str] | None = None,
        mode: str = "hybrid_rerank",
    ) -> list[RetrievalHit]:
        if mode not in {"bm25", "dense", "hybrid", "hybrid_rerank"}:
            raise ValueError("Unsupported retrieval mode")
        if not query.strip() or achievement_ids == []:
            return []
        constraints = constraints or {}
        filters = [Achievement.status == "approved", visibility_filter(organization_id)]
        if achievement_ids is not None:
            filters.append(Achievement.id.in_(achievement_ids))
        if constraints.get("taxonomy_id"):
            filters.append(Achievement.taxonomy_id == constraints["taxonomy_id"])
        minimum = constraints.get("min_maturity_level")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            filters.append(
                or_(Achievement.maturity_level >= minimum, Achievement.maturity_level.is_(None))
            )
        source = settings.retrieval_backend
        fallback_reason = ""
        if source == "elasticsearch":
            try:
                rows = await self._elastic_search(
                    query, organization_id, constraints, achievement_ids, mode
                )
            except Exception as exc:
                if not settings.retrieval_allow_fallback:
                    raise
                logger.warning("search_degraded", reason=type(exc).__name__)
                fallback_reason = type(exc).__name__
                source = "local_fallback"
        if source != "elasticsearch":
            candidates = list((await session.scalars(select(Achievement).where(*filters))).all())
            rows = await asyncio.to_thread(self._local_search, candidates, query, mode)
        # PG remains the authority for ACL, approval and version even when ES lags.
        ids = {row["achievement_id"] for row in rows}
        if not ids:
            return []
        achievements = {
            item.id: item
            for item in (
                await session.scalars(select(Achievement).where(*filters, Achievement.id.in_(ids)))
            ).all()
        }
        sources: dict[str, list[dict]] = defaultdict(list)
        for item in (
            await session.scalars(
                select(AchievementSource).where(AchievementSource.achievement_id.in_(achievements))
            )
        ).all():
            sources[item.achievement_id].append(
                {
                    "id": item.id,
                    "title": item.title,
                    "url": item.url or "",
                    "document_id": item.document_id,
                }
            )
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            item = achievements.get(row["achievement_id"])
            if item is not None and row["version"] == item.version:
                grouped[item.id].append(row)
        provider = get_embedding_provider()
        hits = []
        for achievement_id, matches in grouped.items():
            item = achievements[achievement_id]
            authoritative = {
                c.id: c
                for c in chunk_achievement(item, settings.chunk_size, settings.chunk_overlap)
            }
            evidence = []
            for row in matches:
                chunk = authoritative.get(row["chunk_id"])
                if chunk is not None and chunk.content_hash == row["content_hash"]:
                    evidence.append(
                        {
                            **chunk.as_dict(),
                            "chunk_id": chunk.id,
                            "score": row["score"],
                            "sources": [
                                s
                                for s in sources[item.id]
                                if (
                                    chunk.document_id is None
                                    or s["document_id"] == chunk.document_id
                                )
                            ],
                        }
                    )
            if not evidence:
                continue
            evidence.sort(key=lambda e: e["score"], reverse=True)
            hits.append(
                RetrievalHit(
                    achievement_id=item.id,
                    title=item.title,
                    abstract=item.abstract,
                    score=max(e["score"] for e in evidence),
                    reranker_score=max(e.get("reranker_score", 0) for e in evidence),
                    payload={
                        "status": item.status,
                        "version": item.version,
                        "maturity_level": item.maturity_level,
                        "metrics": item.metrics,
                        "cost_description": item.cost_description,
                        "tags": item.tags,
                        "application_scenarios": item.application_scenarios,
                        "evidence_score": item.evidence_score,
                        "chunks": evidence,
                        "retrieval_backend": source,
                        "retrieval_mode": mode,
                        "embedding_model": provider.model_name,
                        "fallback_reason": fallback_reason,
                    },
                )
            )
        if mode == "hybrid_rerank":
            passages = [(hit, chunk) for hit in hits for chunk in hit.payload["chunks"]]
            scores = await asyncio.to_thread(
                provider.rerank,
                query,
                [f"{hit.title}\n{chunk['text']}" for hit, chunk in passages],
            )
            for (hit, chunk), score in zip(passages, scores, strict=True):
                chunk["reranker_score"] = score
                hit.reranker_score = max(hit.reranker_score, score)
            for hit in hits:
                hit.payload["chunks"].sort(
                    key=lambda e: (e["reranker_score"], e["score"]), reverse=True
                )
        for hit in hits:
            hit.payload["chunks"] = hit.payload["chunks"][:4]
        hits.sort(key=lambda h: (-h.reranker_score, -h.score, h.achievement_id))
        return hits[:limit]

    async def _elastic_search(self, query, organization_id, constraints, achievement_ids, mode):
        provider = get_embedding_provider()
        filters = [
            {"term": {"status": "approved"}},
            {"terms": {"organization_id": ["", organization_id] if organization_id else [""]}},
            {"term": {"embedding_model": provider.model_name}},
        ]
        if achievement_ids is not None:
            filters.append({"terms": {"achievement_id": achievement_ids}})
        if constraints.get("taxonomy_id"):
            filters.append({"term": {"taxonomy_id": constraints["taxonomy_id"]}})
        minimum = constraints.get("min_maturity_level")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            filters.append(
                {
                    "bool": {
                        "minimum_should_match": 1,
                        "should": [
                            {"range": {"maturity_level": {"gte": minimum}}},
                            {"bool": {"must_not": {"exists": {"field": "maturity_level"}}}},
                        ],
                    }
                }
            )
        count = settings.retrieval_candidates
        rankings = []
        if mode != "dense":
            response = await self.search_index.client.search(
                index=settings.elasticsearch_index,
                size=count,
                query={
                    "bool": {
                        "filter": filters,
                        "must": [{"multi_match": {"query": query, "fields": ["title^2", "text"]}}],
                    }
                },
                source_excludes=["embedding"],
            )
            rankings.append(
                [dict(hit["_source"], score=hit["_score"]) for hit in response["hits"]["hits"]]
            )
        if mode != "bm25":
            vector = await asyncio.to_thread(provider.embed, query)
            if any(vector):
                response = await self.search_index.client.search(
                    index=settings.elasticsearch_index,
                    size=count,
                    knn={
                        "field": "embedding",
                        "query_vector": vector,
                        "k": count,
                        "num_candidates": count * settings.knn_num_candidates_factor,
                        "filter": {"bool": {"filter": filters}},
                    },
                    source_excludes=["embedding"],
                )
                rankings.append(
                    [dict(hit["_source"], score=hit["_score"]) for hit in response["hits"]["hits"]]
                )
        return (
            reciprocal_rank_fusion(rankings)
            if mode.startswith("hybrid")
            else (rankings[0] if rankings else [])
        )

    @staticmethod
    def _local_search(achievements, query, mode):
        """Explicit offline smoke backend; never reported as Elasticsearch."""
        provider = get_embedding_provider()
        rows = []
        for item in achievements:
            for chunk in chunk_achievement(item, settings.chunk_size, settings.chunk_overlap):
                rows.append(
                    {
                        **chunk.as_dict(),
                        "chunk_id": chunk.id,
                        "terms": Counter(tokenize(item.title + "\n" + chunk.text)),
                    }
                )
        if not rows:
            return []
        terms = set(tokenize(query))
        avg_len = sum(sum(r["terms"].values()) for r in rows) / len(rows)
        df = {term: sum(term in r["terms"] for r in rows) for term in terms}
        query_vector = provider.embed(query) if mode != "bm25" else []
        for row in rows:
            length = sum(row["terms"].values())
            row["bm25"] = sum(
                math.log(1 + (len(rows) - df[t] + 0.5) / (df[t] + 0.5))
                * (row["terms"][t] * 2.2)
                / (row["terms"][t] + 1.2 * (0.25 + 0.75 * length / max(avg_len, 1)))
                for t in terms
                if row["terms"][t]
            )
            row["dense"] = (
                sum(a * b for a, b in zip(query_vector, provider.embed(row["text"]), strict=True))
                if query_vector
                else 0
            )
        rankings = []
        for key in ["bm25", "dense"] if mode.startswith("hybrid") else [mode]:
            ranked = sorted(rows, key=lambda r: (-r[key], r["chunk_id"]))
            rankings.append(
                [dict(r, score=r[key]) for r in ranked if r[key] > 0][
                    : settings.retrieval_candidates
                ]
            )
        return reciprocal_rank_fusion(rankings) if mode.startswith("hybrid") else rankings[0]
