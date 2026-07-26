from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field
from qdrant_client import models
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.domain import Achievement
from app.services.embeddings import get_embedding_provider
from app.services.vector_store import VectorStoreService

logger = get_logger("retrieval")


class RetrievalHit(BaseModel):
    achievement_id: str
    title: str
    abstract: str = ""
    score: float = 0
    reranker_score: float = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class RetrievalService:
    def __init__(self, vector_store: VectorStoreService) -> None:
        self.vector_store = vector_store

    async def ensure_collection(self) -> None:
        client = self.vector_store.client
        if await client.collection_exists(settings.qdrant_collection):
            return
        await client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=settings.embedding_dimension,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for field_name, schema in (
            ("status", models.PayloadSchemaType.KEYWORD),
            ("organization_id", models.PayloadSchemaType.KEYWORD),
            ("taxonomy_id", models.PayloadSchemaType.KEYWORD),
            ("maturity_level", models.PayloadSchemaType.INTEGER),
        ):
            await client.create_payload_index(
                collection_name=settings.qdrant_collection,
                field_name=field_name,
                field_schema=schema,
            )

    @staticmethod
    def achievement_text(achievement: Achievement) -> str:
        parts = [
            achievement.title,
            achievement.abstract,
            achievement.technical_principle,
            "；".join(achievement.application_scenarios),
            "；".join(achievement.advantages),
            "；".join(achievement.limitations),
            "；".join(achievement.tags),
            "；".join(achievement.organizations),
        ]
        return "\n".join(part for part in parts if part)

    async def index_achievement(self, achievement: Achievement) -> None:
        await self.ensure_collection()
        provider = get_embedding_provider()
        content = self.achievement_text(achievement)
        dense = await asyncio.to_thread(provider.embed, content)
        sparse_indices, sparse_values = await asyncio.to_thread(provider.sparse, content)
        payload = {
            "achievement_id": achievement.id,
            "organization_id": achievement.organization_id or "",
            "taxonomy_id": achievement.taxonomy_id or "",
            "title": achievement.title,
            "abstract": achievement.abstract,
            "status": achievement.status,
            "maturity_level": achievement.maturity_level or 0,
            "version": achievement.version,
            "index_version": settings.knowledge_index_version,
            "embedding_model": provider.model_name,
            "tags": achievement.tags,
            "application_scenarios": achievement.application_scenarios,
            "organizations": achievement.organizations,
        }
        await self.vector_store.client.upsert(
            collection_name=settings.qdrant_collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=achievement.id,
                    vector={
                        "dense": dense,
                        "sparse": models.SparseVector(
                            indices=sparse_indices,
                            values=sparse_values,
                        ),
                    },
                    payload=payload,
                )
            ],
        )

    async def delete_achievement(self, achievement_id: str) -> None:
        if not await self.vector_store.client.collection_exists(settings.qdrant_collection):
            return
        await self.vector_store.client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.PointIdsList(points=[achievement_id]),
            wait=True,
        )

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        organization_id: str | None = None,
        limit: int = 10,
        constraints: dict[str, Any] | None = None,
    ) -> list[RetrievalHit]:
        try:
            return await self._qdrant_search(
                query,
                organization_id=organization_id,
                limit=limit,
                constraints=constraints or {},
            )
        except Exception as exc:
            logger.warning(
                "qdrant_search_fallback",
                reason=type(exc).__name__,
                message=str(exc),
            )
            return await self._database_fallback(session, query, organization_id, limit)

    async def _qdrant_search(
        self,
        query: str,
        *,
        organization_id: str | None,
        limit: int,
        constraints: dict[str, Any],
    ) -> list[RetrievalHit]:
        if not await self.vector_store.client.collection_exists(settings.qdrant_collection):
            return []
        provider = get_embedding_provider()
        dense = await asyncio.to_thread(provider.embed, query)
        sparse_indices, sparse_values = await asyncio.to_thread(provider.sparse, query)

        must_conditions: list[models.Condition] = [
            models.FieldCondition(
                key="status",
                match=models.MatchValue(value="approved"),
            )
        ]
        min_maturity = constraints.get("min_maturity_level")
        if isinstance(min_maturity, int):
            must_conditions.append(
                models.FieldCondition(
                    key="maturity_level",
                    range=models.Range(gte=min_maturity),
                )
            )
        query_filter = models.Filter(
            must=must_conditions,
        )
        result = await self.vector_store.client.query_points(
            collection_name=settings.qdrant_collection,
            prefetch=[
                models.Prefetch(
                    query=dense,
                    using="dense",
                    filter=query_filter,
                    limit=max(limit * 5, 30),
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_indices,
                        values=sparse_values,
                    ),
                    using="sparse",
                    filter=query_filter,
                    limit=max(limit * 5, 30),
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            with_payload=True,
            limit=max(limit * 3, 20),
        )
        provisional = [
            RetrievalHit(
                achievement_id=str(point.payload.get("achievement_id", point.id)),
                title=str(point.payload.get("title", "")),
                abstract=str(point.payload.get("abstract", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in result.points
        ]
        rerank_scores = await asyncio.to_thread(
            provider.rerank,
            query,
            [f"{item.title}\n{item.abstract}" for item in provisional],
        )
        for item, rerank_score in zip(provisional, rerank_scores, strict=True):
            item.reranker_score = rerank_score
        provisional.sort(
            key=lambda item: (item.reranker_score, item.score),
            reverse=True,
        )
        return provisional[:limit]

    async def _database_fallback(
        self,
        session: AsyncSession,
        query: str,
        organization_id: str | None,
        limit: int,
    ) -> list[RetrievalHit]:
        filters = [Achievement.status == "approved"]
        if organization_id:
            filters = [
                or_(
                    Achievement.status == "approved",
                    Achievement.organization_id == organization_id,
                )
            ]
        result = await session.execute(
            select(Achievement)
            .where(*filters)
            .order_by(Achievement.updated_at.desc())
            .limit(500)
        )
        achievements = list(result.scalars())
        provider = get_embedding_provider()
        passages = [self.achievement_text(item) for item in achievements]
        scores = await asyncio.to_thread(provider.rerank, query, passages)
        hits = [
            RetrievalHit(
                achievement_id=achievement.id,
                title=achievement.title,
                abstract=achievement.abstract,
                score=score,
                reranker_score=score,
                payload={
                    "maturity_level": achievement.maturity_level,
                    "status": achievement.status,
                    "tags": achievement.tags,
                },
            )
            for achievement, score in zip(achievements, scores, strict=True)
            if score > 0
        ]
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]
