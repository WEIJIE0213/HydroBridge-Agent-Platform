"""Build a separate BGE index in batches, preserving the previous index for rollback."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

from elasticsearch.helpers import async_bulk
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models.domain import Achievement, AchievementSource
from app.db.models.knowledge import AchievementChunk, OutboxEvent
from app.db.session import AsyncSessionFactory
from app.services.chunking import chunk_achievement
from app.services.embeddings import get_embedding_provider
from app.services.search_index import SearchIndexService


async def build(args):
    settings.embedding_backend = "bge_service"
    settings.elasticsearch_index = args.index
    get_embedding_provider.cache_clear()
    provider = get_embedding_provider()
    health = await asyncio.to_thread(provider.health)
    index = SearchIndexService()
    progress = (
        json.loads(args.progress.read_text(encoding="utf-8")) if args.progress.exists() else {}
    )
    if progress and progress["index"] != args.index:
        raise ValueError("Progress belongs to another index")
    if progress.get("completed") and not await index.client.indices.exists(index=args.index):
        raise ValueError("Resume index is missing; use a new progress file to rebuild all chunks")
    if progress and progress["model"].get("manifest") != health.get("manifest"):
        raise ValueError("Model weights changed; use a new index and progress file")
    await index.ensure_index()
    progress = progress or {"index": args.index, "completed": {}, "chunks": 0, "model": health}
    started = time.monotonic()
    try:
        async with AsyncSessionFactory() as session:
            items = list(
                (
                    await session.scalars(
                        select(Achievement)
                        .where(Achievement.status == "approved")
                        .order_by(Achievement.id)
                    )
                ).all()
            )
            sources = list((await session.scalars(select(AchievementSource))).all())
            sources_by_achievement = defaultdict(list)
            for source in sources:
                sources_by_achievement[source.achievement_id].append(source)
            for offset in range(0, len(items), 50):
                batch = items[offset : offset + 50]
                pairs = []
                changed = []
                for item in batch:
                    corpus_hash = (item.structured_data or {}).get("corpus_fingerprint", "")
                    signature = f"{item.version}:{corpus_hash}"
                    if progress["completed"].get(item.id) == signature:
                        continue
                    changed.append((item, signature))
                    pairs.extend(
                        (item, c)
                        for c in chunk_achievement(
                            item, settings.chunk_size, settings.chunk_overlap
                        )
                    )
                if not changed:
                    continue
                vectors = await asyncio.to_thread(
                    provider.embed_many, [f"{a.title}\n{c.text}" for a, c in pairs]
                )
                actions = []
                for (item, chunk), vector in zip(pairs, vectors, strict=True):
                    actions.append(
                        {
                            "_index": args.index,
                            "_id": chunk.id,
                            "_source": {
                                "chunk_id": chunk.id,
                                "achievement_id": item.id,
                                "version": item.version,
                                "ordinal": chunk.ordinal,
                                "field": chunk.field,
                                "text": chunk.text,
                                "title": item.title,
                                "content_hash": chunk.content_hash,
                                "organization_id": item.organization_id or "",
                                "taxonomy_id": item.taxonomy_id or "",
                                "status": item.status,
                                "maturity_level": item.maturity_level,
                                "embedding": vector,
                                "embedding_model": provider.model_name,
                            },
                        }
                    )
                await async_bulk(index.client, actions, refresh=False, chunk_size=256)
                for item, signature in changed:
                    # Replay only removes old versions of this aggregate in the new index.
                    await index.client.delete_by_query(
                        index=args.index,
                        refresh=False,
                        query={
                            "bool": {
                                "filter": [{"term": {"achievement_id": item.id}}],
                                "must_not": [{"term": {"version": item.version}}],
                            }
                        },
                    )
                    await session.execute(
                        delete(AchievementChunk).where(AchievementChunk.achievement_id == item.id)
                    )
                    for owner, chunk in pairs:
                        if owner.id != item.id:
                            continue
                        session.add(
                            AchievementChunk(
                                id=chunk.id,
                                achievement_id=item.id,
                                version=item.version,
                                ordinal=chunk.ordinal,
                                field=chunk.field,
                                content=chunk.text,
                                start_offset=chunk.start,
                                end_offset=chunk.end,
                                content_hash=chunk.content_hash,
                                source_ids=[
                                    s.id
                                    for s in sources_by_achievement[item.id]
                                    if s.achievement_id == item.id
                                    and (
                                        chunk.document_id is None
                                        or s.document_id == chunk.document_id
                                    )
                                ],
                            )
                        )
                    item.search_indexed = True
                    # The new index was just built from the current authoritative PG version.
                    events = list(
                        (
                            await session.scalars(
                                select(OutboxEvent).where(
                                    OutboxEvent.aggregate_type == "achievement",
                                    OutboxEvent.aggregate_id == item.id,
                                    OutboxEvent.status.in_(["pending", "failed"]),
                                )
                            )
                        ).all()
                    )
                    for event in events:
                        event.status = "completed"
                        event.last_error = ""
                    schema_event = await session.scalar(
                        select(OutboxEvent).where(
                            OutboxEvent.aggregate_type == "achievement",
                            OutboxEvent.aggregate_id == item.id,
                            OutboxEvent.event_type == "achievement.index_schema",
                            OutboxEvent.version == 4,
                        )
                    )
                    if schema_event is None:
                        session.add(
                            OutboxEvent(
                                aggregate_type="achievement",
                                aggregate_id=item.id,
                                event_type="achievement.index_schema",
                                version=4,
                                status="completed",
                                payload={
                                    "achievement_id": item.id,
                                    "index_version": 4,
                                    "index": args.index,
                                },
                            )
                        )
                    progress["completed"][item.id] = signature
                await session.commit()
                progress["chunks"] += len(pairs)
                progress["elapsed_seconds"] = round(time.monotonic() - started, 1)
                args.progress.parent.mkdir(parents=True, exist_ok=True)
                args.progress.write_text(
                    json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            "achievements_done": len(progress["completed"]),
                            "total": len(items),
                            "chunks_written": progress["chunks"],
                            "elapsed_seconds": progress["elapsed_seconds"],
                        }
                    ),
                    flush=True,
                )
            await index.client.indices.refresh(index=args.index)
            progress["es_count"] = (await index.client.count(index=args.index))["count"]
            progress["status"] = "complete"
            progress["corpus_versions_sha256"] = hashlib.sha256(
                json.dumps(progress["completed"], sort_keys=True).encode()
            ).hexdigest()
            args.progress.write_text(
                json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps({k: v for k, v in progress.items() if k not in {"completed", "model"}}),
                flush=True,
            )
    finally:
        await index.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="hydrobridge-chunks-bge-v4")
    parser.add_argument("--progress", type=Path, required=True)
    args = parser.parse_args()
    if not args.index.startswith("hydrobridge-chunks-bge-"):
        raise ValueError("Use a separate BGE index; existing hash indices must be preserved")
    asyncio.run(build(args))


if __name__ == "__main__":
    main()
