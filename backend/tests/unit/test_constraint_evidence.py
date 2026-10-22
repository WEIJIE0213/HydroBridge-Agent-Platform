from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agents.schemas import ConstraintCheck, ConstraintVerdict, ConstraintVerdictBatch
from app.core.config import settings
from app.db.models.domain import Achievement
from app.services.chunking import chunk_achievement
from app.services.matching import EvidenceMatchingWorkflow, apply_evidence_verdicts
from app.services.retrieval import RetrievalService

ABSTRACT = "该技术采用不停水带压开孔工艺，施工期间渠道无需断流，适用于高寒地区的冬季施工。"


def unknown(name: str) -> ConstraintCheck:
    return ConstraintCheck(
        constraint=name, status="unknown", reason="资料未给出可直接核验的同口径指标"
    )


def test_verbatim_quote_resolves_unknown_constraint_with_evidence_method():
    pool = {"c1": {"text": ABSTRACT, "field": "abstract"}}
    checks = apply_evidence_verdicts(
        [unknown("construction_impact")],
        [
            ConstraintVerdict(
                constraint="construction_impact",
                status="satisfied",
                chunk_id="c1",
                quote="施工期间渠道无需断流",
                reason="原文说明施工不断流",
            )
        ],
        pool,
    )
    assert checks[0].status == "satisfied"
    assert checks[0].method == "evidence"
    assert checks[0].chunk_id == "c1" and checks[0].quote == "施工期间渠道无需断流"


def test_fabricated_short_or_foreign_quotes_keep_constraint_unknown():
    pool = {"c1": {"text": ABSTRACT, "field": "abstract"}}
    verdicts = [
        ConstraintVerdict(
            constraint="a", status="satisfied", chunk_id="c1", quote="停水时间小于2小时"
        ),
        ConstraintVerdict(
            constraint="b", status="violated", chunk_id="other", quote="施工期间渠道无需断流"
        ),
        ConstraintVerdict(constraint="c", status="satisfied", chunk_id="c1", quote="高寒"),
    ]
    checks = apply_evidence_verdicts([unknown("a"), unknown("b"), unknown("c")], verdicts, pool)
    assert [c.status for c in checks] == ["unknown", "unknown", "unknown"]
    assert all(c.method == "rule" and "保持未知" in c.reason for c in checks)


def test_model_verdict_never_overrides_rule_result():
    rule = ConstraintCheck(
        constraint="min_maturity_level", status="violated", reason="资料 TRL=6，要求 TRL≥8"
    )
    pool = {"c1": {"text": "该成果技术成熟度达到TRL 9级", "field": "abstract"}}
    checks = apply_evidence_verdicts(
        [rule],
        [
            ConstraintVerdict(
                constraint="min_maturity_level",
                status="satisfied",
                chunk_id="c1",
                quote="技术成熟度达到TRL 9级",
            )
        ],
        pool,
    )
    assert checks == [rule]


async def test_matching_retrieves_constraint_evidence_and_verifies_unknowns(session):
    achievement = Achievement(
        id="pipe-1",
        title="渠道不停水带压修复技术",
        normalized_title="渠道不停水带压修复技术",
        abstract=ABSTRACT,
        status="approved",
        version=1,
        maturity_level=8,
    )
    session.add(achievement)
    await session.commit()
    abstract_chunk = next(c for c in chunk_achievement(achievement) if c.field == "abstract")

    async def verdicts(schema, system_prompt, user_prompt, **kwargs):
        payload = json.loads(user_prompt)
        assert schema is ConstraintVerdictBatch
        assert {c["constraint"] for c in payload["constraints"]} == {
            "construction_impact",
            "climate",
        }
        assert abstract_chunk.id in {e["chunk_id"] for e in payload["evidence"]}
        return ConstraintVerdictBatch(
            verdicts=[
                ConstraintVerdict(
                    constraint="construction_impact",
                    status="satisfied",
                    chunk_id=abstract_chunk.id,
                    quote="施工期间渠道无需断流",
                ),
                # A paraphrase that is not in the source must not be accepted.
                ConstraintVerdict(
                    constraint="climate",
                    status="satisfied",
                    chunk_id=abstract_chunk.id,
                    quote="可在零下30度稳定运行",
                ),
            ]
        )

    retrieval = RetrievalService(None)
    search = AsyncMock(side_effect=retrieval.search)
    model = SimpleNamespace(configured=True, structured=AsyncMock(side_effect=verdicts))
    services = SimpleNamespace(retrieval=SimpleNamespace(search=search))
    deps = SimpleNamespace(session=session, model=model, services=services)
    state = {
        "run_id": "run-1",
        "user_message": "渠道修复，要求不停水，高寒地区，TRL至少7级",
        "requirement_profile": {
            "normalized_description": "渠道不停水修复",
            "hard_constraints": {
                "min_maturity_level": 7,
                "construction_impact": "minimal_shutdown",
                "climate": "cold_region",
            },
        },
    }
    _, batch = await EvidenceMatchingWorkflow().match(state, deps)

    result = batch.results[0]
    statuses = {c.constraint: (c.status, c.method) for c in result.constraint_checks}
    assert statuses == {
        "min_maturity_level": ("satisfied", "rule"),
        "construction_impact": ("satisfied", "evidence"),
        "climate": ("unknown", "rule"),
    }
    assert result.eligibility == "unknown"
    assert abstract_chunk.id in {e.get("chunk_id") for e in result.evidence}
    scoped_call = search.await_args_list[1]
    assert scoped_call.kwargs["achievement_ids"] == ["pipe-1"]
    assert model.structured.await_count == 1


async def test_evidence_verification_is_skipped_without_model_or_beyond_limit(session, monkeypatch):
    achievement = Achievement(
        id="pipe-2",
        title="渠道不停水带压修复技术",
        normalized_title="渠道不停水带压修复技术",
        abstract=ABSTRACT,
        status="approved",
        version=1,
    )
    session.add(achievement)
    await session.commit()
    state = {
        "run_id": "run-2",
        "user_message": "渠道修复，要求不停水",
        "requirement_profile": {
            "normalized_description": "渠道不停水修复",
            "hard_constraints": {"construction_impact": "minimal_shutdown"},
        },
    }
    services = SimpleNamespace(retrieval=RetrievalService(None))
    offline = SimpleNamespace(configured=False, structured=AsyncMock())
    _, batch = await EvidenceMatchingWorkflow().match(
        state, SimpleNamespace(session=session, model=offline, services=services)
    )
    assert batch.results[0].constraint_checks[0].status == "unknown"
    offline.structured.assert_not_awaited()

    monkeypatch.setattr(settings, "match_evidence_verify_limit", 0)
    online = SimpleNamespace(configured=True, structured=AsyncMock())
    await EvidenceMatchingWorkflow().match(
        state, SimpleNamespace(session=session, model=online, services=services)
    )
    online.structured.assert_not_awaited()
