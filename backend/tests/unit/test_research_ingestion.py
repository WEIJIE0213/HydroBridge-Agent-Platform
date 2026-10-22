from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.domain import Achievement, AchievementSource
from app.db.models.identity import User
from app.db.models.knowledge import OutboxEvent
from app.services.research_ingestion import ResearchIngestionService


def candidate(title: str = "泵站设备预测性维护技术") -> dict[str, object]:
    urls = [
        "https://example.gov.cn/results/pump-maintenance",
        "https://example.edu.cn/papers/pump-maintenance",
    ]
    return {
        "title": title,
        "normalized_title": title,
        "organizations": ["某水利科学研究院"],
        "abstract": "融合振动、电流与温度信号识别泵组故障。",
        "technical_principle": "通过多模态时序特征与异常检测模型实现预测性维护。",
        "application_scenarios": ["泵站运维", "设备故障预警"],
        "maturity_level": 8,
        "advantages": ["降低非计划停机"],
        "limitations": ["需要积累现场基线数据"],
        "metrics": [{"name": "故障识别准确率", "value": "92%"}],
        "evidence": [
            {
                "field": "technical_principle",
                "claim": "采用多模态异常检测",
                "evidence_text": "系统融合振动、电流和温度信号进行异常检测。",
                "source_url": urls[0],
                "support_type": "direct",
                "confidence": 0.95,
            },
            {
                "field": "metrics",
                "claim": "故障识别准确率达到92%",
                "evidence_text": "现场测试故障识别准确率为92%。",
                "source_url": urls[1],
                "support_type": "direct",
                "confidence": 0.9,
            },
        ],
        "source_urls": urls,
        "evidence_score": 0.95,
    }


def source_results() -> list[dict[str, object]]:
    return [
        {
            "title": "泵站设备预测性维护科技成果",
            "url": "https://example.gov.cn/results/pump-maintenance",
            "source_type": "official_result_catalog",
            "authority_level": "official",
            "authority_score": 0.98,
            "relevance_score": 0.96,
            "focus_coverage": 0.9,
            "query_coverage": 0.85,
            "temporal_status": "in_range",
            "low_evidence_signals": [],
            "marketing_signals": [],
        },
        {
            "title": "泵组多模态异常检测研究",
            "url": "https://example.edu.cn/papers/pump-maintenance",
            "source_type": "research_paper",
            "authority_level": "academic",
            "authority_score": 0.9,
            "relevance_score": 0.92,
            "focus_coverage": 0.85,
            "query_coverage": 0.8,
            "temporal_status": "in_range",
            "low_evidence_signals": [],
            "marketing_signals": [],
        },
    ]


def test_three_way_confidence_routing() -> None:
    service = ResearchIngestionService()
    strong = candidate("高置信度成果")
    grey = candidate("灰区成果")
    grey["organizations"] = []
    rejected = candidate("低质量营销成果")
    rejected["source_urls"] = ["https://vendor.example.com/product"]
    rejected["evidence"] = []
    sources = [
        *source_results(),
        {
            "title": "产品推广页",
            "url": "https://vendor.example.com/product",
            "source_type": "web_page",
            "authority_level": "general_web",
            "authority_score": 0.2,
            "relevance_score": 0.1,
            "focus_coverage": 0,
            "query_coverage": 0.1,
            "temporal_status": "unknown",
            "low_evidence_signals": ["缺少技术证据"],
            "marketing_signals": ["立即购买"],
        },
    ]

    scored = service.score_candidates([strong, grey, rejected], sources)

    assert [item["route"] for item in scored] == [
        "auto_approved",
        "human_review",
        "rejected",
    ]
    assert "完成单位或作者信息缺失" in scored[1]["review_reasons"]
    assert scored[2]["hard_rejection_reasons"]


def test_batch_review_is_capped_and_overflow_is_deferred() -> None:
    service = ResearchIngestionService()
    records: list[dict[str, object]] = []
    for index in range(service.thresholds.review_limit + 2):
        item = deepcopy(candidate(f"灰区成果 {index}"))
        item["organizations"] = []
        records.append(item)

    scored = service.score_candidates(records, source_results())

    assert sum(item["route"] == "human_review" for item in scored) == 5
    assert sum(item["route"] == "deferred" for item in scored) == 2


@pytest.mark.asyncio
async def test_approved_ingestion_is_idempotent_and_emits_one_outbox_event(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    service = ResearchIngestionService()
    approved = service.score_candidates([candidate()], source_results())
    assert approved[0]["route"] == "auto_approved"

    first_ids = await service.persist_approved(
        session,
        approved,
        organization_id=seeded_user.organization_id,
        run_id="research-run-1",
        approval_mode="automatic",
    )
    second_ids = await service.persist_approved(
        session,
        approved,
        organization_id=seeded_user.organization_id,
        run_id="research-run-1",
        approval_mode="automatic",
    )

    assert first_ids == second_ids
    assert await session.scalar(select(func.count()).select_from(Achievement)) == 1
    assert await session.scalar(select(func.count()).select_from(AchievementSource)) == 2
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
