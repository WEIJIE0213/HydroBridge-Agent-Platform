from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.identity import User
from app.db.models.knowledge import Artifact
from app.main import app


@pytest.mark.usefixtures("client")
async def test_artifact_metadata_and_authenticated_download(
    client: httpx.AsyncClient,
    session: AsyncSession,
    seeded_user: User,
    auth_headers: dict[str, str],
) -> None:
    content = "# HydroBridge 报告\n\n已完成成果匹配。".encode()
    artifact = Artifact(
        user_id=seeded_user.id,
        artifact_type="agent_report",
        title="Agent 分析报告",
        mime_type="text/markdown; charset=utf-8",
        object_key="runs/test/final-report.md",
        sha256=hashlib.sha256(content).hexdigest(),
        metadata_json={"encoding": "utf-8"},
    )
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)

    app.state.services.object_storage.get_bytes = AsyncMock(return_value=content)

    metadata = await client.get(
        f"/api/v1/artifacts/{artifact.id}",
        headers=auth_headers,
    )
    assert metadata.status_code == 200
    assert metadata.json()["sha256"] == hashlib.sha256(content).hexdigest()

    download = await client.get(
        f"/api/v1/artifacts/{artifact.id}/download",
        headers=auth_headers,
    )
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["x-content-sha256"] == artifact.sha256
    assert "attachment;" in download.headers["content-disposition"]


async def test_artifact_download_requires_authentication(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/artifacts/does-not-exist/download")
    assert response.status_code == 401
