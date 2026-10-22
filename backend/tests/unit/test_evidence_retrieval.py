from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.agents.graph import TOOLS, HydroBridgeGraph
from app.core.config import settings
from app.db.models.domain import Achievement
from app.db.models.knowledge import AchievementChunk
from app.evaluate_retrieval import metrics
from app.services.chunking import chunk_achievement, field_text
from app.services.matching import check_constraints
from app.services.rag import AgenticRag, GroundedAnswer, GroundedClaim, validate_claims
from app.services.retrieval import RetrievalHit, RetrievalService, reciprocal_rank_fusion


def record(**kwargs):
    fields = dict(
        id="water-1",
        title="泵站监测",
        normalized_title="泵站监测",
        abstract="设备连续监测振动和温度，发现异常后通知工作人员。",
        status="approved",
        version=1,
        maturity_level=8,
    )
    return Achievement(**(fields | kwargs))


def test_chunks_preserve_original_field_offsets_and_identity():
    item = record(abstract="技术原理。" * 80, metrics=[{"指标": "能耗", "值": "0.3kWh"}])
    first = chunk_achievement(item, 100, 20)
    assert len([c for c in first if c.field == "abstract"]) > 1
    assert first == chunk_achievement(item, 100, 20)
    for chunk in first:
        assert field_text(getattr(item, chunk.field))[chunk.start : chunk.end] == chunk.text
    item.version = 2
    assert not {c.id for c in first} & {c.id for c in chunk_achievement(item, 100, 20)}
    with pytest.raises(ValueError, match="overlap"):
        chunk_achievement(item, 100, 100)


def test_constraints_need_same_units_and_original_evidence():
    item = record()
    chunks = [c.as_dict() for c in chunk_achievement(item)]
    checks = check_constraints(
        {
            "min_maturity_level": 9,
            "cost_description": "预算100万元",
            "maturity_level": {"max": 10, "unit": "年"},
        },
        item,
        chunks,
    )
    assert [c.status for c in checks] == ["violated", "unknown", "unknown"]
    assert checks[0].quote == "8"
    assert check_constraints({"min_maturity_level": 7}, item, chunks)[0].status == "satisfied"
    assert check_constraints({"min_maturity_level": 7}, item, [])[0].status == "unknown"
    assert check_constraints({"min_maturity_level": True}, item, chunks)[0].status == "unknown"
    item.maturity_level = None
    assert check_constraints({"min_maturity_level": 7}, item, chunks)[0].status == "unknown"


def test_rrf_uses_rank_and_does_not_double_count_within_one_list():
    a, b = {"chunk_id": "a", "score": 999}, {"chunk_id": "b", "score": 0.001}
    results = reciprocal_rank_fusion([[a, a, b], [b]])
    assert [r["chunk_id"] for r in results] == ["b", "a"]
    assert results[1]["score"] == pytest.approx(1 / 61)


async def test_search_filters_tenant_status_scope_and_preserves_unknown_trl(session):
    session.add_all(
        [
            record(),
            record(id="foreign", organization_id="other"),
            record(id="draft", status="candidate"),
            record(id="unknown-trl", maturity_level=None, title="未知成熟度监测"),
            record(id="low-trl", maturity_level=3, title="低成熟度监测"),
        ]
    )
    await session.commit()
    service = RetrievalService(None)
    hits = await service.search(
        session, "泵站监测振动", organization_id="own", constraints={"min_maturity_level": 7}
    )
    assert {h.achievement_id for h in hits} == {"water-1", "unknown-trl"}
    assert len({h.achievement_id for h in hits}) == len(hits)
    assert all(h.payload["chunks"] and h.payload["retrieval_backend"] == "local" for h in hits)
    assert not await service.search(session, "泵站监测", achievement_ids=[])
    scoped = await service.search(session, "泵站监测", achievement_ids=["water-1"])
    assert [h.achievement_id for h in scoped] == ["water-1"]


async def test_stale_or_revoked_elasticsearch_hits_cannot_escape_pg_recheck(session, monkeypatch):
    item = record()
    session.add_all([item, record(id="foreign", organization_id="other")])
    await session.commit()
    chunks = [{**c.as_dict(), "chunk_id": c.id, "score": 1} for c in chunk_achievement(item)]
    service = RetrievalService(None)
    monkeypatch.setattr(settings, "retrieval_backend", "elasticsearch")
    monkeypatch.setattr(service, "_elastic_search", AsyncMock(return_value=chunks))
    assert await service.search(session, "监测")
    item.version = 2
    await session.commit()
    assert not await service.search(session, "监测")
    item.version, item.status = 1, "candidate"
    await session.commit()
    assert not await service.search(session, "监测")


async def test_es_failures_are_visible_unless_fallback_was_explicit(session, monkeypatch):
    session.add(record())
    await session.commit()
    service = RetrievalService(None)
    monkeypatch.setattr(settings, "retrieval_backend", "elasticsearch")
    monkeypatch.setattr(service, "_elastic_search", AsyncMock(side_effect=ConnectionError("down")))
    with pytest.raises(ConnectionError):
        await service.search(session, "监测")
    monkeypatch.setattr(settings, "retrieval_allow_fallback", True)
    hits = await service.search(session, "监测")
    assert hits[0].payload["retrieval_backend"] == "local_fallback"
    assert hits[0].payload["fallback_reason"] == "ConnectionError"


async def test_chunk_index_replay_is_idempotent_and_deletion_clears_chunks(session):
    item = record()
    session.add(item)
    await session.commit()
    service = RetrievalService(None)
    for _ in range(2):
        await service.index_achievement(item, session)
        await session.commit()
    assert await session.scalar(select(func.count()).select_from(AchievementChunk)) == len(
        chunk_achievement(item)
    )
    old_ids = set((await session.scalars(select(AchievementChunk.id))).all())
    item.version = 2
    await service.index_achievement(item, session)
    await session.commit()
    assert not old_ids & set((await session.scalars(select(AchievementChunk.id))).all())
    await service.delete_achievement(item.id, session)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(AchievementChunk)) == 0


async def test_es_bm25_and_knn_receive_identical_hard_filters(monkeypatch):
    client = SimpleNamespace(search=AsyncMock(return_value={"hits": {"hits": []}}))
    service = RetrievalService(SimpleNamespace(client=client))
    await service._elastic_search("监测", "own", {"min_maturity_level": 7}, ["water-1"], "hybrid")
    lexical, dense = [call.kwargs for call in client.search.await_args_list]
    assert lexical["query"]["bool"]["filter"] == dense["knn"]["filter"]["bool"]["filter"]
    filters = lexical["query"]["bool"]["filter"]
    assert {"terms": {"organization_id": ["", "own"]}} in filters
    assert {"terms": {"achievement_id": ["water-1"]}} in filters
    assert {"term": {"status": "approved"}} in filters


def test_fabricated_quotes_and_unknown_chunk_ids_are_rejected():
    answer = GroundedAnswer(
        claims=[
            GroundedClaim(text="a", chunk_id="known", quote="真实原文"),
            GroundedClaim(text="b", chunk_id="known", quote="不存在的数字92%"),
            GroundedClaim(text="c", chunk_id="foreign", quote="真实原文"),
        ]
    )
    assert [c.text for c in validate_claims(answer, {"known": {"text": "真实原文"}})] == ["a"]


async def test_rag_stops_after_two_rounds_keeps_scope_and_rejects_false_sufficiency():
    chunk = next(c for c in chunk_achievement(record()) if c.field == "abstract")
    evidence = {**chunk.as_dict(), "chunk_id": chunk.id}
    retrieval = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                RetrievalHit(
                    achievement_id="water-1", title="泵站监测", payload={"chunks": [evidence]}
                )
            ]
        )
    )
    model = SimpleNamespace(
        configured=True,
        structured=AsyncMock(
            side_effect=[
                GroundedAnswer(next_query="监测成本", missing_information=["缺少成本"]),
                GroundedAnswer(
                    sufficient=True,
                    next_query="再次查询",
                    claims=[
                        GroundedClaim(text="成本低于10万元", chunk_id=chunk.id, quote="成本10万元")
                    ],
                ),
            ]
        ),
    )
    deps = SimpleNamespace(services=SimpleNamespace(retrieval=retrieval), model=model, session=None)
    result = await AgenticRag().answer(
        {"run_id": "run", "organization_id": "own"},
        deps,
        query="成本是多少",
        achievement_ids=["water-1"],
    )
    assert result["rounds"] == 2 and not result["sufficient"]
    assert result["citations"] == []
    assert "成本低于10万元" not in result["answer"]
    for call in retrieval.search.await_args_list:
        assert call.kwargs["achievement_ids"] == ["water-1"]
        assert call.kwargs["organization_id"] == "own"


def test_agent_exposes_five_tools_and_explicit_empty_scope_is_not_widened():
    assert len(TOOLS) == 5
    assert (
        HydroBridgeGraph._scope(
            {"context": {"achievement_ids": []}, "selected_achievement_ids": ["previous"]}
        )
        == []
    )
    assert HydroBridgeGraph._scope({"selected_achievement_ids": ["previous"]}) == ["previous"]
    assert HydroBridgeGraph._scope({"scope_is_set": True, "selected_achievement_ids": []}) == []


def test_recall_and_mrr_use_ranked_ground_truth_not_matching_scores():
    values = metrics(["wrong", "relevant", "other"], {"relevant", "missing"})
    assert values == {"recall@1": 0, "recall@5": 0.5, "recall@10": 0.5, "mrr@10": 0.5}
