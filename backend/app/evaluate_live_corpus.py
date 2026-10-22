"""Read-only ablation against the current PostgreSQL/Elasticsearch corpus."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models.domain import Achievement
from app.db.models.identity import User
from app.db.session import AsyncSessionFactory
from app.services.embeddings import get_embedding_provider
from app.services.retrieval import RetrievalService
from app.services.search_index import SearchIndexService


async def evaluate(args):
    labels_bytes = await asyncio.to_thread(args.cases.read_bytes)
    labels = json.loads(labels_bytes)
    index = SearchIndexService()
    retrieval = RetrievalService(index)
    details = []
    try:
        async with AsyncSessionFactory() as session:
            user = await session.scalar(
                select(User).where(User.email == settings.bootstrap_admin_email)
            )
            titles = set(
                (
                    await session.scalars(
                        select(Achievement.title).where(
                            Achievement.status == "approved",
                            Achievement.organization_id == user.organization_id,
                        )
                    )
                ).all()
            )
            for case in labels["cases"]:
                if not set(case["relevant_titles"]) <= titles:
                    raise ValueError(f"Labels missing from live corpus: {case['id']}")
            for mode in ("bm25", "dense", "hybrid", "hybrid_rerank"):
                for case in labels["cases"]:
                    started = time.perf_counter()
                    hits = await retrieval.search(
                        session,
                        case["query"],
                        organization_id=user.organization_id,
                        mode=mode,
                        limit=10,
                    )
                    ranked = [h.title for h in hits]
                    relevant = set(case["relevant_titles"])
                    row = {
                        "mode": mode,
                        "case_id": case["id"],
                        "query": case["query"],
                        "relevant_titles": case["relevant_titles"],
                        "ranked_titles": ranked,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        **{f"hit@{k}": int(bool(set(ranked[:k]) & relevant)) for k in (1, 5, 10)},
                        "mrr@10": next(
                            (1 / n for n, t in enumerate(ranked[:10], 1) if t in relevant), 0
                        ),
                        "top_evidence": [
                            {
                                "title": h.title,
                                "reranker_score": h.reranker_score,
                                "chunks": [
                                    {
                                        k: c.get(k)
                                        for k in (
                                            "chunk_id",
                                            "document_id",
                                            "locator",
                                            "field",
                                            "text",
                                        )
                                    }
                                    for c in h.payload["chunks"][:1]
                                ],
                            }
                            for h in hits[:3]
                        ],
                    }
                    details.append(row)
                print(
                    json.dumps(
                        {
                            "mode": mode,
                            "cases": len(labels["cases"]),
                            "hit@1": sum(d["hit@1"] for d in details if d["mode"] == mode)
                            / len(labels["cases"]),
                        }
                    ),
                    flush=True,
                )
        summary = {}
        for mode in ("bm25", "dense", "hybrid", "hybrid_rerank"):
            rows = [d for d in details if d["mode"] == mode]
            latencies = sorted(r["latency_ms"] for r in rows)
            summary[mode] = {
                **{
                    k: round(statistics.mean(r[k] for r in rows), 4)
                    for k in ("hit@1", "hit@5", "hit@10", "mrr@10")
                },
                "latency_p50_ms": round(statistics.median(latencies), 2),
                "latency_p95_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
            }
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "index": settings.elasticsearch_index,
            "backend": settings.retrieval_backend,
            "embedding_model": get_embedding_provider().model_name,
            "reranker_model": settings.reranker_model,
            "case_count": len(labels["cases"]),
            "corpus_size": len(titles),
            "chunk_count": (await index.client.count(index=settings.elasticsearch_index))["count"],
            "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
            "label_status": labels["label_status"],
            "metric_definition": (
                "Hit@k: any labeled target appears in top k; MRR: rank of first labeled target. "
                "Labels are developer acceptance cases, not exhaustive relevance judgments."
            ),
            "summary": summary,
            "details": details,
        }
        await asyncio.to_thread(
            args.output.write_text,
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        await index.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    main()
