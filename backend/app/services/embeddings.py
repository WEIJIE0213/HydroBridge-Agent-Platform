from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("embeddings")


def tokenize(text: str) -> list[str]:
    normalized = text.casefold()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)
    chinese = [token for token in words if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    bigrams = [f"{chinese[index]}{chinese[index + 1]}" for index in range(len(chinese) - 1)]
    return words + bigrams


class HashEmbeddingProvider:
    """Deterministic offline provider used by tests and as a no-download fallback."""

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension or settings.embedding_dimension
        self.model_name = "hydrobridge-hash-embedding-v1"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token, frequency in Counter(tokenize(text)).items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * (1 + math.log(frequency))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector

    def sparse(self, text: str) -> tuple[list[int], list[float]]:
        counts = Counter(tokenize(text))
        values: dict[int, float] = {}
        for token, frequency in counts.items():
            index = int.from_bytes(
                hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(),
                "big",
            ) % 1_000_003
            values[index] = values.get(index, 0) + 1 + math.log(frequency)
        ordered = sorted(values.items())
        return [item[0] for item in ordered], [item[1] for item in ordered]

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        query_terms = set(tokenize(query))
        scores = []
        for passage in passages:
            passage_terms = set(tokenize(passage))
            matched = len(query_terms & passage_terms)
            query_coverage = matched / max(1, len(query_terms))
            bounded_precision = matched / max(
                1,
                min(len(passage_terms), len(query_terms) * 3),
            )
            scores.append(query_coverage * 0.85 + bounded_precision * 0.15)
        return scores


class BgeM3EmbeddingProvider:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel, FlagReranker

        self.model_name = settings.embedding_model
        self.model = BGEM3FlagModel(settings.embedding_model, use_fp16=False)
        self.reranker = FlagReranker(settings.reranker_model, use_fp16=False)
        self.dimension = settings.embedding_dimension

    def embed(self, text: str) -> list[float]:
        output = self.model.encode([text], return_dense=True, return_sparse=True)
        return output["dense_vecs"][0].tolist()

    def sparse(self, text: str) -> tuple[list[int], list[float]]:
        output = self.model.encode([text], return_dense=True, return_sparse=True)
        weights = output["lexical_weights"][0]
        ordered = sorted((int(index), float(value)) for index, value in weights.items())
        return [item[0] for item in ordered], [item[1] for item in ordered]

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        raw = self.reranker.compute_score(
            [[query, passage] for passage in passages],
            normalize=True,
        )
        return [float(score) for score in raw]


@lru_cache(maxsize=1)
def get_embedding_provider() -> HashEmbeddingProvider | BgeM3EmbeddingProvider:
    if settings.embedding_backend in {"auto", "bge_m3"}:
        try:
            provider = BgeM3EmbeddingProvider()
            logger.info("embedding_provider_loaded", provider="bge_m3")
            return provider
        except (ImportError, OSError, RuntimeError) as exc:
            if settings.embedding_backend == "bge_m3":
                raise
            logger.warning(
                "embedding_provider_fallback",
                provider="hash",
                reason=type(exc).__name__,
            )
    return HashEmbeddingProvider()
