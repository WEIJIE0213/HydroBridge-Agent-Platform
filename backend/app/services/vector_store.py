from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from app.core.config import settings


class VectorStoreService:
    def __init__(self) -> None:
        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=15,
            check_compatibility=False,
        )

    async def health(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()
