from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.domain import Achievement, AchievementSource
from app.db.models.knowledge import Document, DocumentChunk, OutboxEvent
from app.importers.corpus import import_package
from app.services.chunking import chunk_achievement
from app.services.embeddings import BgeServiceEmbeddingProvider, get_embedding_provider
from app.services.rag import locate_original_quote
from app.services.retrieval import RetrievalService


def test_structured_quote_links_only_an_associated_verbatim_original():
    documents = [
        {"document_id": "unrelated", "segments": [{"text": "单通道35km", "locator": "第1页"}]},
        {"document_id": "source", "segments": [{"text": "指标：单通道35km。", "locator": "行12"}]},
    ]
    located = locate_original_quote(documents, "单通道35km", {"source"})
    assert located["document_id"] == "source"
    assert located["locator"] == "行12"
    assert (
        documents[1]["segments"][0]["text"][
            located["source_quote_start"] : located["source_quote_end"]
        ]
        == "单通道35km"
    )
    assert locate_original_quote(documents, "单通道50km", {"source"}) == {}
    assert locate_original_quote(documents, "单通道35km", set()) == {}


def test_source_chunks_preserve_document_locator_and_skip_duplicate_representations():
    original = "长距离岩石顶管施工纪录为3224m。" * 20
    item = Achievement(
        id="a",
        title="顶管",
        version=1,
        structured_data={
            "source_documents": [
                {
                    "sha256": "a" * 64,
                    "document_id": "d1",
                    "segments": [
                        {"text": original, "locator": "PDF 第 3 页"},
                        {
                            "text": "duplicate representation",
                            "locator": "Sheet1!A2:D2",
                            "index": False,
                        },
                    ],
                },
            ]
        },
    )
    chunks = [c for c in chunk_achievement(item, 100, 20) if c.document_id]
    assert len(chunks) > 1
    assert all(c.document_id == "d1" and c.locator == "PDF 第 3 页" for c in chunks)
    assert all(original[c.start : c.end] == c.text for c in chunks)
    assert not any("duplicate" in c.text for c in chunks)


@respx.mock
def test_service_validates_identity_and_vectors_and_preserves_single_rerank(monkeypatch):
    monkeypatch.setattr(settings, "embedding_dimension", 128)
    provider = BgeServiceEmbeddingProvider()
    route = respx.post(provider.base_url + "/embed")
    route.mock(
        return_value=httpx.Response(
            200, json={"model": "BAAI/bge-m3", "vectors": [[1.0] + [0.0] * 127]}
        )
    )
    assert len(provider.embed("测试")) == 128
    route.mock(return_value=httpx.Response(200, json={"model": "wrong-model", "vectors": []}))
    with pytest.raises(ValueError, match="unexpected model"):
        provider.embed("测试")
    route.mock(
        return_value=httpx.Response(200, json={"model": "BAAI/bge-m3", "vectors": [[0.0] * 128]})
    )
    with pytest.raises(ValueError, match="invalid vectors"):
        provider.embed("测试")
    respx.post(provider.base_url + "/rerank").mock(
        return_value=httpx.Response(200, json={"model": "BAAI/bge-reranker-v2-m3", "scores": [0.8]})
    )
    assert provider.rerank("查询", ["段落"]) == [0.8]
    assert provider.rerank("查询", []) == []


@respx.mock
def test_explicit_service_outage_does_not_fall_back_to_hash(monkeypatch):
    monkeypatch.setattr(settings, "embedding_backend", "bge_service")
    get_embedding_provider.cache_clear()
    provider = get_embedding_provider()
    respx.post(provider.base_url + "/embed").mock(side_effect=httpx.ConnectError("unavailable"))
    with pytest.raises(httpx.ConnectError):
        provider.embed("测试")
    get_embedding_provider.cache_clear()


async def test_corpus_replay_deduplicates_sources_and_retrieves_full_text(session, tmp_path):
    data = b"source document"
    source = tmp_path / "original.txt"
    source.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    original = "监测系统的采样周期为17秒，异常数据通过短信发送。"
    package = {
        "documents": [
            {
                "sha256": digest,
                "document_id": "doc1",
                "local_path": str(source),
                "filename": "原始证明.txt",
                "paths": [str(source)],
            }
        ],
        "records": [
            {
                "title": "遥测",
                "normalized_title": "遥测",
                "source_refs": [],
                "catalog_ids": [],
                "structured_variants": [],
                "source_documents": [
                    {
                        "sha256": digest,
                        "document_id": "doc1",
                        "segments": [{"text": original, "locator": "PDF 第 8 页", "page": 8}],
                    }
                ],
            }
        ],
    }
    storage = SimpleNamespace(
        ensure_buckets=AsyncMock(), put_bytes=AsyncMock(), document_bucket="test"
    )
    first = await import_package(package, session, storage, "org")
    second = await import_package(package, session, storage, "org")
    assert first["achievements_created"] == 1
    assert second["achievements_unchanged"] == 1
    for model in [Achievement, Document, DocumentChunk, AchievementSource, OutboxEvent]:
        assert await session.scalar(select(func.count()).select_from(model)) == 1
    assert storage.put_bytes.await_count == 1
    # Query a fact absent from the title, with real source locator and provenance.
    service = RetrievalService(None)
    hits = await service.search(session, "17秒 采样周期", organization_id="org")
    document_chunks = [c for c in hits[0].payload["chunks"] if c.get("document_id")]
    assert document_chunks
    assert document_chunks[0]["locator"] == "PDF 第 8 页"
    assert document_chunks[0]["sources"][0]["document_id"] == "doc1"
