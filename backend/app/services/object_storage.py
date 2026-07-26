from __future__ import annotations

import asyncio
from io import BytesIO

from minio import Minio

from app.core.config import settings


class ObjectStorageService:
    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.document_bucket = settings.minio_bucket_documents
        self.artifact_bucket = settings.minio_bucket_artifacts

    async def ensure_buckets(self) -> None:
        for bucket in (self.document_bucket, self.artifact_bucket):
            exists = await asyncio.to_thread(self.client.bucket_exists, bucket)
            if not exists:
                await asyncio.to_thread(self.client.make_bucket, bucket)

    async def health(self) -> bool:
        try:
            await asyncio.to_thread(lambda: list(self.client.list_buckets()))
            return True
        except Exception:
            return False

    async def put_bytes(
        self,
        bucket: str,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            bucket,
            object_key,
            BytesIO(content),
            len(content),
            content_type,
        )

    async def get_bytes(self, bucket: str, object_key: str) -> bytes:
        response = await asyncio.to_thread(
            self.client.get_object,
            bucket,
            object_key,
        )
        try:
            return await asyncio.to_thread(response.read)
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)
