from __future__ import annotations

import httpx


async def test_requirement_crud_is_user_scoped(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    created = await client.post(
        "/api/v1/requirements",
        headers=auth_headers,
        json={
            "title": "灌区渠道渗漏治理",
            "raw_input": "某灌区渠道渗漏严重，预算有限，希望尽量减少停水施工时间。",
        },
    )
    assert created.status_code == 201, created.text
    requirement = created.json()

    updated = await client.patch(
        f"/api/v1/requirements/{requirement['id']}",
        headers=auth_headers,
        json={
            "normalized_description": "寻找低成本、短停水周期的灌区渠道防渗技术",
            "scenario": "灌区输水渠道",
            "hard_constraints": {"max_shutdown_days": 3},
            "soft_preferences": {"low_cost": True},
            "completeness_score": 0.88,
            "status": "ready",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "ready"
    assert updated.json()["hard_constraints"]["max_shutdown_days"] == 3

    listing = await client.get("/api/v1/requirements", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
