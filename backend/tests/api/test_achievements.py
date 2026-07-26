from __future__ import annotations

import httpx


async def test_create_list_and_approve_achievement(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    create = await client.post(
        "/api/v1/achievements",
        headers=auth_headers,
        json={
            "title": "灌区渠道智能防渗监测技术",
            "abstract": "利用传感器与模型识别渠道渗漏位置。",
            "application_scenarios": ["灌区渠道", "输水工程"],
            "organizations": ["某水利科学研究院"],
            "maturity_level": 7,
            "tags": ["渠道防渗", "智能监测"],
        },
    )
    assert create.status_code == 201, create.text
    achievement = create.json()
    assert achievement["status"] == "candidate"
    assert achievement["normalized_title"] == "灌区渠道智能防渗监测技术"

    listing = await client.get("/api/v1/achievements", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    decision = await client.post(
        f"/api/v1/achievements/{achievement['id']}/decision",
        headers=auth_headers,
        json={"decision": "approve", "comment": "证据完整，同意入库"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"


async def test_achievement_endpoint_requires_authentication(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/achievements")
    assert response.status_code == 401
