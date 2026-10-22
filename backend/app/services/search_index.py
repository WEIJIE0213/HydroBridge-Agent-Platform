from __future__ import annotations

from elasticsearch import AsyncElasticsearch, BadRequestError

from app.core.config import settings


class SearchIndexService:
    def __init__(self) -> None:
        self.client = AsyncElasticsearch(
            settings.elasticsearch_url,
            api_key=settings.elasticsearch_api_key or None,
            request_timeout=20,
            max_retries=2,
            retry_on_timeout=True,
        )

    async def ensure_index(self) -> None:
        index = settings.elasticsearch_index
        if await self.client.indices.exists(index=index):
            mapping = await self.client.indices.get_mapping(index=index)
            properties = mapping[index]["mappings"]["properties"]
            if properties["embedding"]["dims"] != settings.embedding_dimension:
                raise ValueError("Embedding dimension changed: create a new index and reindex")
            return
        analyzer = settings.elasticsearch_analyzer
        properties = {
            key: {"type": "keyword"}
            for key in (
                "achievement_id",
                "organization_id",
                "taxonomy_id",
                "status",
                "field",
                "chunk_id",
                "content_hash",
                "embedding_model",
            )
        }
        properties.update(
            {
                "title": {"type": "text", "analyzer": analyzer},
                "text": {"type": "text", "analyzer": analyzer},
                "version": {"type": "integer"},
                "maturity_level": {"type": "integer"},
                "ordinal": {"type": "integer"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.embedding_dimension,
                    "index": True,
                    "similarity": "cosine",
                    "index_options": {
                        "type": settings.elasticsearch_vector_index_type,
                        "m": settings.elasticsearch_hnsw_m,
                        "ef_construction": settings.elasticsearch_hnsw_ef_construction,
                    },
                },
            }
        )
        try:
            await self.client.indices.create(
                index=index,
                mappings={"properties": properties},
                settings={"number_of_shards": 1, "number_of_replicas": 0},
            )
        except BadRequestError as exc:
            if exc.error != "resource_already_exists_exception":
                raise

    async def health(self) -> bool:
        try:
            return bool(await self.client.ping()) and bool(
                await self.client.indices.exists(index=settings.elasticsearch_index)
            )
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()
