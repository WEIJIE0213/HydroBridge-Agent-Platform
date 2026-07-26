from app.services.embeddings import HashEmbeddingProvider, tokenize


def test_chinese_tokenizer_includes_characters_and_bigrams() -> None:
    tokens = tokenize("农村供水 monitoring")

    assert "农" in tokens
    assert "农村" in tokens
    assert "供水" in tokens
    assert "monitoring" in tokens


def test_offline_reranker_rewards_query_coverage_not_document_brevity() -> None:
    provider = HashEmbeddingProvider(dimension=128)
    scores = provider.rerank(
        "农村供水泵站智能监测低维护",
        [
            (
                "农村饮水安全工程关键技术，包含供水泵站运行监测、"
                "智能告警和低维护设计，并具有多个工程应用案例。"
            ),
            "水库大坝混凝土防渗墙爆破施工与结构加固技术。",
        ],
    )

    assert scores[0] > scores[1] + 0.25
