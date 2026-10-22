from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.domain import RequirementRefinement
from app.db.models.runtime import AgentRun, ApprovalTask
from app.main import app


@pytest.fixture
def isolated_agent_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.services.events.publish = AsyncMock(return_value="1")
    monkeypatch.setattr(
        "app.api.routes.achievements.dispatch_agent_run",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "app.api.routes.requirements.dispatch_agent_run",
        lambda *_: None,
    )


async def test_achievement_evaluation_workflow_is_versioned(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    isolated_agent_dispatch: None,
) -> None:
    created = await client.post(
        "/api/v1/achievements",
        headers=auth_headers,
        json={
            "title": "水库大坝智能巡检技术",
            "abstract": "融合图像识别与传感数据进行安全巡检。",
            "maturity_level": 7,
        },
    )
    achievement_id = created.json()["id"]

    first = await client.post(
        f"/api/v1/achievements/{achievement_id}/evaluations",
        headers=auth_headers,
    )
    second = await client.post(
        f"/api/v1/achievements/{achievement_id}/evaluations",
        headers=auth_headers,
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["evaluation_version"] == 1
    assert second.json()["evaluation_version"] == 2

    history = await client.get(
        f"/api/v1/achievements/{achievement_id}/evaluations",
        headers=auth_headers,
    )
    latest = await client.get(
        "/api/v1/achievements/evaluations/latest",
        headers=auth_headers,
    )
    assert [item["evaluation_version"] for item in history.json()] == [2, 1]
    assert latest.json()[0]["evaluation"]["id"] == second.json()["id"]


async def test_requirement_refinement_and_matching_create_business_records(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    isolated_agent_dispatch: None,
) -> None:
    created = await client.post(
        "/api/v1/requirements",
        headers=auth_headers,
        json={
            "title": "高寒农村供水监测",
            "raw_input": "高寒地区农村供水工程需要低功耗监测，要求维护简单。",
        },
    )
    requirement_id = created.json()["id"]
    refinement = await client.post(
        f"/api/v1/requirements/{requirement_id}/refinements",
        headers=auth_headers,
    )
    match = await client.post(
        f"/api/v1/requirements/{requirement_id}/matches",
        headers=auth_headers,
    )
    assert refinement.status_code == 202, refinement.text
    assert match.status_code == 202, match.text
    assert refinement.json()["refinement_version"] == 1
    assert match.json()["status"] == "queued"

    refinements = await client.get(
        f"/api/v1/requirements/{requirement_id}/refinements",
        headers=auth_headers,
    )
    matches = await client.get(
        f"/api/v1/requirements/{requirement_id}/matches",
        headers=auth_headers,
    )
    summaries = await client.get(
        "/api/v1/requirements/workflow-summaries",
        headers=auth_headers,
    )
    assert len(refinements.json()) == 1
    assert len(matches.json()) == 1
    assert summaries.json()[0]["latest_match_id"] == match.json()["id"]


async def test_clarification_decision_requeues_domain_workflow(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    isolated_agent_dispatch: None,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await client.post(
        "/api/v1/requirements",
        headers=auth_headers,
        json={
            "title": "农村供水监测需求",
            "raw_input": "想找一个供水监测方案。",
        },
    )
    requirement_id = created.json()["id"]
    refinement_response = await client.post(
        f"/api/v1/requirements/{requirement_id}/refinements",
        headers=auth_headers,
    )
    refinement_id = refinement_response.json()["id"]
    run_id = refinement_response.json()["agent_run_id"]

    refinement = await session.get(RequirementRefinement, refinement_id)
    run = await session.get(AgentRun, run_id)
    assert refinement is not None
    assert run is not None
    refinement.status = "waiting_review"
    run.status = "waiting_review"
    approval = ApprovalTask(
        run_id=run_id,
        requested_by_agent="human_review",
        risk_level="medium",
        reason="需求关键信息不足",
        payload={"payload": {"type": "clarification"}},
        status="pending",
    )
    session.add(approval)
    await session.commit()
    await session.refresh(approval)

    execute = AsyncMock()
    monkeypatch.setattr(app.state.services.agent_runtime, "execute", execute)
    monkeypatch.setattr(settings, "agent_execution_mode", "inline")
    decided = await client.post(
        f"/api/v1/runs/{run_id}/decisions",
        headers=auth_headers,
        json={
            "approval_id": approval.id,
            "decision": "edit",
            "comment": "已补充现场约束",
            "edited_payload": {
                "message": "农村供水泵站需要低功耗监测，改造期间不能长时间停水。"
            },
        },
    )

    assert decided.status_code == 200, decided.text
    session.expire_all()
    refreshed_run = await session.get(AgentRun, run_id)
    refreshed_refinement = await session.get(RequirementRefinement, refinement_id)
    assert refreshed_run is not None and refreshed_run.status == "queued"
    assert refreshed_refinement is not None and refreshed_refinement.status == "queued"
    execute.assert_awaited_once()
