"""Reproducible retrieval ablation; isolated corpus/index, no production DB writes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.models import import_all_models
from app.db.models.domain import Achievement
from app.services.embeddings import get_embedding_provider
from app.services.retrieval import RetrievalService
from app.services.search_index import SearchIndexService


def metrics(ranked: list[str], relevant: set[str]) -> dict[str, float]:
    if not relevant:
        raise ValueError("Evaluation cases require at least one relevant document")
    result = {f"recall@{k}": len(set(ranked[:k]) & relevant) / len(relevant) for k in (1, 5, 10)}
    result["mrr@10"] = next(
        (1 / rank for rank, doc in enumerate(ranked[:10], 1) if doc in relevant), 0
    )
    return result


async def evaluate(args):
    seed_bytes = await asyncio.to_thread(args.seed.read_bytes)
    labels_bytes = await asyncio.to_thread(args.cases.read_bytes)
    records, labels = json.loads(seed_bytes), json.loads(labels_bytes)
    titles = {r["title"] for r in records}
    for case in labels["cases"]:
        missing = set(case["relevant_titles"]) - titles
        if missing:
            raise ValueError(f"Label titles absent from corpus: {missing}")
    settings.retrieval_backend = args.backend
    settings.retrieval_allow_fallback = False
    settings.embedding_backend = args.embedding_backend
    settings.elasticsearch_index = "hb-eval-" + uuid.uuid4().hex
    get_embedding_provider.cache_clear()
    provider = get_embedding_provider()
    index = SearchIndexService()
    service = RetrievalService(index)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    import_all_models()
    details = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for record in records:
                fields = {k: v for k, v in record.items() if k in Achievement.__table__.columns}
                item = Achievement(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, record["title"])), **fields
                )
                session.add(item)
                await session.flush()
                await service.index_achievement(item, session)
            await session.commit()
            for mode in ("bm25", "dense", "hybrid", "hybrid_rerank"):
                for case in labels["cases"]:
                    start = time.perf_counter()
                    hits = await service.search(session, case["query"], mode=mode, limit=10)
                    ranked = [h.title for h in hits]
                    details.append(
                        {
                            "mode": mode,
                            "case_id": case["id"],
                            "query": case["query"],
                            "ranked_titles": ranked,
                            "relevant_titles": case["relevant_titles"],
                            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                            **metrics(ranked, set(case["relevant_titles"])),
                        }
                    )
        summary = {}
        for mode in ("bm25", "dense", "hybrid", "hybrid_rerank"):
            rows = [d for d in details if d["mode"] == mode]
            summary[mode] = {
                key: round(sum(r[key] for r in rows) / len(rows), 4)
                for key in ("recall@1", "recall@5", "recall@10", "mrr@10")
            }
        return {
            "created_at": datetime.now(UTC).isoformat(),
            "backend": args.backend,
            "analyzer": settings.elasticsearch_analyzer
            if args.backend == "elasticsearch"
            else "offline_character_bigram_smoke",
            "embedding_model": provider.model_name,
            "reranker": settings.reranker_model
            if args.embedding_backend == "bge_m3"
            else "offline_token_overlap_smoke",
            "corpus_size": len(records),
            "case_count": len(labels["cases"]),
            "seed_sha256": hashlib.sha256(seed_bytes).hexdigest(),
            "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
            "label_status": labels["label_status"],
            "purpose": labels["purpose"],
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "candidate_limit": settings.retrieval_candidates,
            "summary": summary,
            "details": details,
        }
    finally:
        if args.backend == "elasticsearch":
            await index.client.indices.delete(
                index=settings.elasticsearch_index, ignore_unavailable=True
            )
        await index.close()
        await engine.dispose()


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=root / "seed-data/achievements.json")
    parser.add_argument("--cases", type=Path, default=root / "evaluation/retrieval_cases.json")
    parser.add_argument("--output", type=Path, default=root / "evaluation/results/local-hash.json")
    parser.add_argument("--backend", choices=["local", "elasticsearch"], default="local")
    parser.add_argument("--embedding-backend", choices=["hash", "bge_m3"], default="hash")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "backend": report["backend"],
                "embedding_model": report["embedding_model"],
                "summary": report["summary"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
