from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_overview_returns_user_dashboard_metrics(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.get("/api/v1/overview", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["achievements"]["total"] == 0
    assert payload["requirements"] == 0
    assert payload["pending_approvals"] == 0
