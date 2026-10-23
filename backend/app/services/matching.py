from __future__ import annotations

import json
from typing import Any

from app.agents.prompts import CONSTRAINT_EVIDENCE_SYSTEM_PROMPT
from app.agents.schemas import (
    ConstraintCheck,
    ConstraintVerdict,
    ConstraintVerdictBatch,
    MatchAssessment,
    MatchAssessmentBatch,
)
from app.core.logging import get_logger
from app.services.chunking import chunk_achievement
from app.services.retrieval import retrieval_notices

logger = get_logger("matching")

# A judgement must rest on a quote long enough to carry meaning on its own;
# a two-character fragment such as "适用" proves nothing.
MIN_EVIDENCE_QUOTE_CHARS = 6
MAX_EVIDENCE_CHUNKS = 12


def check_constraints(constraints: dict, achievement, chunks: list[dict]) -> list[ConstraintCheck]:
    checks = []
    for key, expected in constraints.items():
        field = "maturity_level" if key == "min_maturity_level" else key
        actual = getattr(achievement, field, None)
        status, reason = "unknown", "资料未给出可直接核验的同口径指标"
        evidence = next((c for c in chunks if c["field"] == field), None)
        if (
            key == "min_maturity_level"
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and actual is not None
        ):
            status = "satisfied" if actual >= expected else "violated"
            reason = f"资料 TRL={actual}，要求 TRL≥{expected}"
        elif (
            isinstance(expected, dict)
            and isinstance(actual, (int, float))
            and not isinstance(actual, bool)
        ):
            lower, upper = expected.get("min"), expected.get("max")
            if (
                (lower is None or isinstance(lower, (int, float)))
                and (upper is None or isinstance(upper, (int, float)))
                and (lower is not None or upper is not None)
                and not expected.get("unit")
            ):
                passed = (lower is None or actual >= lower) and (upper is None or actual <= upper)
                status = "satisfied" if passed else "violated"
                reason = f"资料值={actual}，要求范围={expected}"
        elif isinstance(expected, bool) and isinstance(actual, bool):
            status = "satisfied" if actual == expected else "violated"
            reason = f"资料值={actual}，要求={expected}"
        if status != "unknown" and evidence is None:
            status, reason = "unknown", "缺少对应原文片段，不能确认约束"
        checks.append(
            ConstraintCheck(
                constraint=key,
                status=status,
                reason=reason,
                chunk_id=evidence["id"] if evidence else "",
                quote=evidence["text"] if evidence else "",
            )
        )
    return checks


def apply_evidence_verdicts(
    checks: list[ConstraintCheck],
    verdicts: list[ConstraintVerdict],
    evidence: dict[str, dict],
) -> list[ConstraintCheck]:
    """Resolve rule-level unknowns only with verdicts whose quote is verbatim evidence.

    Rule results are never overridden. A model verdict is accepted only when it cites
    a chunk from the authoritative evidence pool and its quote occurs in that chunk.
    """
    by_constraint: dict[str, ConstraintVerdict] = {}
    for verdict in verdicts:
        by_constraint.setdefault(verdict.constraint, verdict)
    resolved = []
    for check in checks:
        verdict = by_constraint.get(check.constraint)
        if check.status != "unknown" or verdict is None or verdict.status == "unknown":
            resolved.append(check)
            continue
        chunk = evidence.get(verdict.chunk_id)
        quote = verdict.quote.strip()
        if chunk is None or len(quote) < MIN_EVIDENCE_QUOTE_CHARS or quote not in chunk["text"]:
            resolved.append(
                check.model_copy(
                    update={"reason": f"{check.reason}；模型判断缺少可逐字核对的原文引文，保持未知"}
                )
            )
            continue
        resolved.append(
            ConstraintCheck(
                constraint=check.constraint,
                status=verdict.status,
                reason=verdict.reason.strip() or "原文证据核验",
                chunk_id=verdict.chunk_id,
                quote=quote,
                method="evidence",
            )
        )
    return resolved


class EvidenceMatchingWorkflow:
    """A fixed retrieval/aggregation/reranking/constraint workflow, exposed as one tool."""

    @staticmethod
    def _expected_text(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    async def _verify_with_evidence(
        self,
        state,
        deps,
        *,
        hit,
        achievement,
        constraints: dict,
        checks: list[ConstraintCheck],
        all_chunks: list[dict],
        warnings: list[str],
    ) -> list[ConstraintCheck]:
        """RAG evidence check for constraints that field rules could not decide."""
        unknown = [c.constraint for c in checks if c.status == "unknown"]
        if not unknown:
            return checks
        authoritative = {c["id"]: c for c in all_chunks}
        pool: dict[str, dict] = {}

        def collect(chunks: list[dict]) -> None:
            for chunk in chunks:
                chunk_id = chunk.get("chunk_id") or chunk.get("id")
                if chunk_id in authoritative and len(pool) < MAX_EVIDENCE_CHUNKS:
                    pool.setdefault(chunk_id, authoritative[chunk_id])

        # Retrieve evidence aimed at the constraints themselves, inside this achievement.
        query = "；".join(f"{key}：{self._expected_text(constraints[key])}" for key in unknown)
        try:
            scoped = await deps.services.retrieval.search(
                deps.session,
                query,
                organization_id=state.get("organization_id"),
                achievement_ids=[achievement.id],
                limit=1,
            )
        except Exception as exc:
            logger.warning("constraint_evidence_retrieval_failed", error_type=type(exc).__name__)
            warnings.append("部分约束的补充取证失败，已使用现有片段核验；证据不足的约束保持未知。")
            scoped = []
        warnings.extend(retrieval_notices(scoped))
        for item in scoped:
            collect(item.payload.get("chunks", []))
        collect(hit.payload.get("chunks", []))
        if not pool:
            return checks
        payload = {
            "achievement": achievement.title,
            "constraints": [{"constraint": key, "expected": constraints[key]} for key in unknown],
            "evidence": [
                {"chunk_id": chunk_id, "field": chunk["field"], "text": chunk["text"]}
                for chunk_id, chunk in pool.items()
            ],
        }
        try:
            batch = await deps.model.structured(
                ConstraintVerdictBatch,
                CONSTRAINT_EVIDENCE_SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False),
                session=deps.session,
                run_id=state["run_id"],
                agent_name="constraint_evidence_verifier",
            )
        except Exception as exc:
            logger.warning("constraint_evidence_model_failed", error_type=type(exc).__name__)
            return checks
        return apply_evidence_verdicts(checks, batch.verdicts, pool)

    async def match(self, state, deps):
        from sqlalchemy import select

        from app.core.config import settings
        from app.db.models.domain import Achievement
        from app.services.retrieval import visibility_filter

        requirement = state.get("requirement_profile", {})
        query = requirement.get("normalized_description") or state["user_message"]
        constraints = requirement.get("hard_constraints", {})
        context = state.get("context", {})
        scope = context.get("achievement_ids")
        if context.get("achievement_id"):
            scope = [context["achievement_id"]]
        if scope is None and (state.get("scope_is_set") or state.get("selected_achievement_ids")):
            scope = state.get("selected_achievement_ids", [])
        hits = await deps.services.retrieval.search(
            deps.session,
            query,
            organization_id=state.get("organization_id"),
            constraints=constraints,
            achievement_ids=scope,
            limit=10,
        )
        warnings = retrieval_notices(hits)
        records = {
            a.id: a
            for a in (
                await deps.session.scalars(
                    select(Achievement).where(
                        Achievement.id.in_([h.achievement_id for h in hits]),
                        Achievement.status == "approved",
                        visibility_filter(state.get("organization_id")),
                    )
                )
            ).all()
        }
        assessments = []
        for position, hit in enumerate(hits):
            achievement = records.get(hit.achievement_id)
            evidence = hit.payload.get("chunks", [])
            if achievement is None:
                continue
            all_chunks = [
                c.as_dict()
                for c in chunk_achievement(achievement, settings.chunk_size, settings.chunk_overlap)
            ]
            checks = check_constraints(constraints, achievement, all_chunks)
            if deps.model.configured and position < settings.match_evidence_verify_limit:
                checks = await self._verify_with_evidence(
                    state,
                    deps,
                    hit=hit,
                    achievement=achievement,
                    constraints=constraints,
                    checks=checks,
                    all_chunks=all_chunks,
                    warnings=warnings,
                )
            cited = {c.get("chunk_id", c.get("id")) for c in evidence}
            for check in checks:
                if check.chunk_id and check.chunk_id not in cited:
                    chunk = next(c for c in all_chunks if c["id"] == check.chunk_id)
                    evidence.append({**chunk, "chunk_id": chunk["id"], "sources": []})
                    cited.add(check.chunk_id)
            eligibility = (
                "violated"
                if any(c.status == "violated" for c in checks)
                else "unknown"
                if any(c.status == "unknown" for c in checks)
                else "satisfied"
            )
            score = max(0, min(100, hit.reranker_score * 100))
            # The score describes retrieval relevance only. Unknown costs/benefits
            # must not be fabricated as business scores.
            assessments.append(
                MatchAssessment(
                    achievement_id=hit.achievement_id,
                    title=hit.title,
                    rank=1,
                    final_score=round(score, 2),
                    scenario_fit=round(score, 2),
                    effectiveness=0,
                    maturity=(achievement.maturity_level or 0) / 9 * 100,
                    evidence_quality=achievement.evidence_score * 100,
                    cost_fit=0,
                    implementation_risk=100,
                    reasons=[f"检索相关度 {score:.1f}，不代表工程适用性已验证"],
                    gaps=[c.reason for c in checks if c.status == "unknown"],
                    risks=[c.reason for c in checks if c.status == "violated"],
                    cited_source_ids=list(
                        dict.fromkeys(s["id"] for c in evidence for s in c.get("sources", []))
                    ),
                    constraint_checks=checks,
                    eligibility=eligibility,
                    evidence=evidence,
                    score_kind="retrieval_relevance",
                )
            )
        assessments.sort(key=lambda x: (x.eligibility == "violated", -x.final_score))
        for rank, item in enumerate(assessments, 1):
            item.rank = rank
        return [h.model_dump() for h in hits], MatchAssessmentBatch(
            generation_mode="evidence_workflow",
            retrieval_warnings=list(dict.fromkeys(warnings)),
            results=assessments,
            overall_summary=(
                "已按成果聚合检索片段并重排；硬约束先按字段规则核验，"
                "规则无法判定的再检索原文证据核验，未知项保留待确认。"
            ),
        )
