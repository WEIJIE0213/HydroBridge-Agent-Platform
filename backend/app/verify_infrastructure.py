"""Exercise real PostgreSQL, ES/IK, outbox replay and checkpoint reconnect in isolated resources."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace
from typing import TypedDict

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.models import import_all_models
from app.db.models.domain import Achievement
from app.db.models.knowledge import AchievementChunk, OutboxEvent
from app.services.embeddings import get_embedding_provider
from app.services.retrieval import RetrievalService
from app.services.search_index import SearchIndexService


class ReviewState(TypedDict, total=False):
    approved: bool


def review_graph(saver):
    graph = StateGraph(ReviewState)
    graph.add_node("review", lambda _: {"approved": interrupt({"question": "approve?"})})
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=saver)


async def no_op():
    return None


async def verify():
    if make_url(settings.database_url).get_backend_name() != "postgresql":
        raise ValueError("This check requires DATABASE_URL=postgresql+asyncpg://...")
    suffix = uuid.uuid4().hex
    schema = "hb_smoke_" + suffix
    settings.elasticsearch_index = "hb-smoke-" + suffix
    settings.retrieval_backend, settings.retrieval_allow_fallback = "elasticsearch", False
    settings.embedding_backend = "hash"
    get_embedding_provider.cache_clear()
    admin_engine = create_async_engine(settings.database_url)
    engine = create_async_engine(
        settings.database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    index = SearchIndexService()
    retrieval = RetrievalService(index)
    report = {
        "postgres": False,
        "ik": False,
        "outbox_replay": False,
        "hybrid_retrieval": False,
        "stale_version_rejected": False,
        "approval_revocation": False,
        "checkpoint_reconnect": False,
    }
    import_all_models()
    schema_created = False
    index_created = False
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        report["postgres"] = True
        await index.ensure_index()
        index_created = True
        tokens = await index.client.indices.analyze(
            index=settings.elasticsearch_index, analyzer="ik_max_word", text="水利工程安全监测"
        )
        assert tokens["tokens"], "IK analyzer did not return tokens"
        report["ik"] = True
        async with factory() as session:
            item = Achievement(
                title="验收泵站监测",
                normalized_title="验收泵站监测",
                abstract="监测泵站设备振动和温度，识别异常工况。",
                status="approved",
            )
            session.add(item)
            await session.flush()
            item_id = item.id
            for event_type in ("achievement.approved", "achievement.index_schema"):
                session.add(
                    OutboxEvent(
                        aggregate_type="achievement",
                        aggregate_id=item.id,
                        event_type=event_type,
                        version=1,
                    )
                )
            await session.commit()
        # Run the production claim/index/result implementation with only its
        # session factory and service container pointed at the isolated resources.
        from app.worker import tasks

        tasks.AsyncSessionFactory = factory
        tasks.ServiceContainer = SimpleNamespace(
            create=lambda: SimpleNamespace(retrieval=retrieval, close=no_op)
        )
        processed = await tasks._process_outbox()
        assert processed == {"claimed": 2, "completed": 2, "failed": 0}, processed
        async with factory() as session:
            chunk_count = await session.scalar(select(func.count()).select_from(AchievementChunk))
            assert (
                chunk_count
                and (await index.client.count(index=settings.elasticsearch_index))["count"]
                == chunk_count
            )
            report["outbox_replay"] = True
            hits = await retrieval.search(session, "泵站振动监测")
            assert len(hits) == 1 and hits[0].achievement_id == item_id
            assert hits[0].payload["retrieval_backend"] == "elasticsearch"
            report["hybrid_retrieval"] = True
            item = await session.get(Achievement, item_id)
            item.version += 1
            await session.commit()
            assert not await retrieval.search(session, "泵站振动监测")
            report["stale_version_rejected"] = True
            item.status = "rejected"
            session.add(
                OutboxEvent(
                    aggregate_type="achievement",
                    aggregate_id=item_id,
                    event_type="achievement.revoked",
                    version=2,
                )
            )
            await session.commit()
        assert (await tasks._process_outbox())["completed"] == 1
        assert (await index.client.count(index=settings.elasticsearch_index))["count"] == 0
        report["approval_revocation"] = True
        dsn = (
            make_url(settings.database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        config = {"configurable": {"thread_id": "review-reconnect"}}
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, row_factory=dict_row, options=f"-c search_path={schema}"
        ) as connection:
            saver = AsyncPostgresSaver(connection)
            await saver.setup()
            paused = await review_graph(saver).ainvoke({}, config=config)
            assert paused["__interrupt__"]
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, row_factory=dict_row, options=f"-c search_path={schema}"
        ) as connection:
            resumed = await review_graph(AsyncPostgresSaver(connection)).ainvoke(
                Command(resume=True), config=config
            )
            assert resumed["approved"] is True
        report["checkpoint_reconnect"] = True
        return report
    finally:
        try:
            if index_created:
                await index.client.indices.delete(
                    index=settings.elasticsearch_index, ignore_unavailable=True
                )
        finally:
            await index.close()
            await engine.dispose()
            if schema_created:
                async with admin_engine.begin() as conn:
                    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin_engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False, indent=2))
