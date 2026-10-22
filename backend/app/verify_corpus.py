"""Verify all imported source bytes, identities and document provenance against a package."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

from elasticsearch.helpers import async_scan
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.domain import Achievement, AchievementSource
from app.db.models.knowledge import AchievementChunk, Document, DocumentChunk, OutboxEvent
from app.db.session import AsyncSessionFactory
from app.importers.corpus import fingerprint
from app.services.chunking import chunk_achievement
from app.services.embeddings import get_embedding_provider
from app.services.object_storage import ObjectStorageService
from app.services.search_index import SearchIndexService


async def verify(args):
    package = json.loads(await asyncio.to_thread(args.package.read_text, encoding="utf-8"))
    storage = ObjectStorageService()
    semaphore = asyncio.Semaphore(4)
    async with AsyncSessionFactory() as session:
        documents = {d.id: d for d in (await session.scalars(select(Document))).all()}

        async def check(source):
            async with semaphore:
                document = documents[source["document_id"]]
                content = await storage.get_bytes(storage.document_bucket, document.object_key)
                assert hashlib.sha256(content).hexdigest() == source["sha256"] == document.sha256
                assert len(content) == source["size_bytes"] == document.size_bytes
                return len(content)

        sizes = await asyncio.gather(*(check(s) for s in package["documents"]))
        items = {a.normalized_title: a for a in (await session.scalars(select(Achievement))).all()}
        for record in package["records"]:
            item = items[record["normalized_title"]]
            assert item.structured_data["corpus_fingerprint"] == fingerprint(record)
        counts = {}
        for model in (Achievement, Document, DocumentChunk, AchievementSource, AchievementChunk):
            counts[model.__tablename__] = await session.scalar(
                select(func.count()).select_from(model)
            )
        report = {
            "passed": True,
            "originals_verified_sha256": len(sizes),
            "total_source_bytes": sum(sizes),
            "achievement_fingerprints_verified": len(package["records"]),
            "counts": counts,
            "file_types": dict(
                Counter(Path(d["filename"]).suffix.lower() for d in package["documents"])
            ),
            "outbox": dict(
                (
                    await session.execute(
                        select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
                    )
                ).all()
            ),
            "pending_index_achievements": await session.scalar(
                select(func.count())
                .select_from(Achievement)
                .where(Achievement.search_indexed.is_(False))
            ),
            "audit": dict(Counter(i["kind"] for i in package["audit"])),
        }
        if args.verify_index:
            assert report["pending_index_achievements"] == 0
            authoritative = {
                chunk.id: chunk
                for item in items.values()
                if item.status == "approved"
                for chunk in chunk_achievement(item, settings.chunk_size, settings.chunk_overlap)
            }
            pg_chunks = (await session.scalars(select(AchievementChunk))).all()
            assert {chunk.id for chunk in pg_chunks} == set(authoritative)
            for chunk in pg_chunks:
                expected = authoritative[chunk.id]
                assert chunk.content == expected.text
                assert chunk.content_hash == expected.content_hash
                assert chunk.version == expected.version
                assert (chunk.start_offset, chunk.end_offset) == (expected.start, expected.end)
            index = SearchIndexService()
            try:
                model_name = get_embedding_provider().model_name
                indexed = set()
                async for hit in async_scan(
                    index.client,
                    index=settings.elasticsearch_index,
                    query={
                        "query": {"match_all": {}},
                        "_source": ["chunk_id", "content_hash", "version", "embedding_model"],
                    },
                ):
                    row = hit["_source"]
                    expected = authoritative[row["chunk_id"]]
                    assert row["content_hash"] == expected.content_hash
                    assert row["version"] == expected.version
                    assert row["embedding_model"] == model_name
                    indexed.add(row["chunk_id"])
                assert indexed == set(authoritative)
                report["index_verification"] = {
                    "index": settings.elasticsearch_index,
                    "embedding_model": model_name,
                    "chunks_compared_pg_source_and_es": len(indexed),
                    "all_ids_versions_hashes_equal": True,
                }
            finally:
                await index.close()
    await asyncio.to_thread(
        args.output.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-index", action="store_true")
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
