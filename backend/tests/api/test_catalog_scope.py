from __future__ import annotations

from datetime import UTC, datetime

from app.db.models.domain import Achievement
from app.db.models.identity import Organization
from app.db.models.knowledge import Document


async def test_approved_foreign_records_are_not_public_and_public_records_are_read_only(
    client,
    session,
    seeded_user,
    auth_headers,
):
    seeded_user.is_superuser = False
    foreign_org = Organization(name="其他单位", slug="foreign", organization_type="research")
    session.add(foreign_org)
    await session.flush()
    own = Achievement(
        title="本单位",
        normalized_title="本单位",
        status="candidate",
        organization_id=seeded_user.organization_id,
    )
    foreign = Achievement(
        title="其他单位成果",
        normalized_title="其他单位成果",
        status="approved",
        organization_id=foreign_org.id,
    )
    public = Achievement(title="公共成果", normalized_title="公共成果", status="approved")
    session.add_all([own, foreign, public])
    await session.commit()
    response = await client.get("/api/v1/achievements", headers=auth_headers)
    assert {item["id"] for item in response.json()["items"]} == {own.id, public.id}
    assert (
        await client.get(f"/api/v1/achievements/{foreign.id}", headers=auth_headers)
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/achievements/{public.id}", headers=auth_headers, json={"abstract": "不应修改"}
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/achievements/{foreign.id}/decision",
            headers=auth_headers,
            json={"decision": "approve"},
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/runs",
            headers=auth_headers,
            json={
                "message": "评价成果",
                "task_type": "evaluation",
                "context": {"achievement_id": foreign.id},
            },
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/runs",
            headers=auth_headers,
            json={
                "message": "覆盖记录",
                "context": {"domain_action": "achievement_evaluation", "evaluation_id": "other"},
            },
        )
    ).status_code == 400


async def test_catalog_search_reaches_beyond_first_page_without_full_source_payload(
    client,
    session,
    seeded_user,
    auth_headers,
):
    session.add_all(
        [
            Achievement(
                id=f"catalog-{124-i:03d}",
                title=f"测试成果 {i}",
                normalized_title=f"测试成果{i}",
                status="approved",
                organization_id=seeded_user.organization_id,
                structured_data={"source_documents": [{"text": "large source text" * 1000}]},
                updated_at=datetime(2026, 10, 19, tzinfo=UTC),
            )
            for i in range(125)
        ]
    )
    await session.commit()
    page = (
        await client.get("/api/v1/achievements?page_size=24&page=2", headers=auth_headers)
    ).json()
    assert page["total"] == 125 and len(page["items"]) == 24
    assert all(item["structured_data"] == {} for item in page["items"])
    # Bulk imports share timestamps. A unique tie-breaker prevents page overlap/skips in PG.
    first = (
        await client.get("/api/v1/achievements?page_size=24&page=1", headers=auth_headers)
    ).json()
    assert [item["id"] for item in first["items"]] == [f"catalog-{i:03d}" for i in range(24)]
    assert [item["id"] for item in page["items"]] == [f"catalog-{i:03d}" for i in range(24, 48)]
    result = (
        await client.get("/api/v1/achievements?query=测试成果%20124", headers=auth_headers)
    ).json()
    assert result["total"] == 1 and result["items"][0]["title"] == "测试成果 124"


async def test_original_documents_require_authentication_and_tenant_ownership(
    client,
    session,
    seeded_user,
    auth_headers,
):
    seeded_user.is_superuser = False
    foreign = Organization(name="原件所属单位", slug="other-source")
    session.add(foreign)
    await session.flush()
    source = Document(
        organization_id=foreign.id,
        filename="原件.pdf",
        mime_type="application/pdf",
        sha256="a" * 64,
        object_key="source.pdf",
    )
    session.add(source)
    await session.commit()
    url = f"/api/v1/documents/{source.id}/download"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=auth_headers)).status_code == 403
