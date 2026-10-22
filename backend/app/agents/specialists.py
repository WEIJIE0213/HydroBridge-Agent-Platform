from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.model_gateway import ModelGateway
from app.agents.prompts import (
    ACHIEVEMENT_EVALUATION_SYSTEM_PROMPT,
    ASSESSMENT_AGENT_SYSTEM_PROMPT,
    EVIDENCE_CURATOR_SYSTEM_PROMPT,
    FINAL_REPORT_SYSTEM_PROMPT,
    REQUIREMENT_ANALYST_SYSTEM_PROMPT,
    RESEARCH_AGENT_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
    VERIFICATION_AGENT_SYSTEM_PROMPT,
)
from app.agents.schemas import (
    CuratedAchievement,
    CuratedAchievementBatch,
    EvidenceClaim,
    IntrinsicAchievementEvaluation,
    IntrinsicScorecard,
    MatchAssessment,
    MatchAssessmentBatch,
    RequirementProfile,
    ResearchPlan,
    SupervisorDecision,
    VerificationResult,
)
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.domain import Achievement
from app.services.container import ServiceContainer
from app.services.research_ranking import (
    ResearchRankingService,
    canonicalize_url,
    requested_date_range,
)

logger = get_logger("agent_specialists")


@dataclass
class AgentDependencies:
    session: AsyncSession
    services: ServiceContainer
    model: ModelGateway


async def safe_structured[SchemaT: BaseModel](
    schema: type[SchemaT],
    system_prompt: str,
    user_prompt: str,
    deps: AgentDependencies,
    state: AgentState,
    agent_name: str,
) -> SchemaT | None:
    try:
        return await deps.model.structured(
            schema,
            system_prompt,
            user_prompt,
            session=deps.session,
            run_id=state["run_id"],
            agent_name=agent_name,
        )
    except Exception as exc:
        logger.warning(
            "model_structured_output_fallback",
            agent=agent_name,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


async def safe_text(
    system_prompt: str,
    user_prompt: str,
    deps: AgentDependencies,
    state: AgentState,
    agent_name: str,
) -> str | None:
    try:
        return await deps.model.text(
            system_prompt,
            user_prompt,
            session=deps.session,
            run_id=state["run_id"],
            agent_name=agent_name,
        )
    except Exception as exc:
        logger.warning(
            "model_text_output_fallback",
            agent=agent_name,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


def accepted_search_results(state: AgentState) -> list[dict[str, Any]]:
    """Return search results that the evidence curator actually accepted."""
    accepted_urls = {
        str(url)
        for record in state.get("evidence_records", [])
        for url in record.get("source_urls", [])
        if url
    }
    return [
        item
        for item in state.get("search_results", [])
        if str(item.get("url", "")) in accepted_urls
    ]


def focused_excerpt(text: str, focus_terms: list[str], limit: int = 800) -> str:
    """Return a compact evidence window centered on the first matched focus term."""

    if len(text) <= limit:
        return text
    positions = [text.find(term) for term in focus_terms if term and term in text]
    if not positions:
        return text[:limit]
    match_position = min(positions)
    # Keep the matched phrase inside the short claim preview, while retaining
    # enough leading context to make the excerpt readable.
    start = max(0, match_position - min(120, limit // 4))
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


class SupervisorAgent:
    name = "supervisor"

    async def decide(self, state: AgentState, deps: AgentDependencies) -> SupervisorDecision:
        message = state["user_message"]
        explicit = state.get("task_type", "auto")
        if deps.model.configured and explicit == "auto":
            generated = await safe_structured(
                SupervisorDecision,
                SUPERVISOR_SYSTEM_PROMPT,
                f"显式任务类型：{explicit}\n用户请求：{message}",
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return generated

        task_type = self._heuristic_route(message, explicit)
        plan_map = {
            "research": [
                "制定检索计划",
                "搜索权威来源",
                "整理成果证据",
                "独立核验",
                "形成报告",
            ],
            "requirement_analysis": [
                "提取工程场景",
                "识别约束与偏好",
                "检查缺失信息",
                "形成规范需求",
            ],
            "evaluation": ["定位目标成果", "按评价规则评分", "核验证据", "形成评估报告"],
            "matching": ["诊断需求", "检索候选成果", "多维匹配评分", "独立核验", "形成推荐报告"],
            "qa": ["检索已审核资料", "组织证据", "生成有来源的回答"],
        }
        return SupervisorDecision(
            task_type=task_type,  # type: ignore[arg-type]
            plan=plan_map[task_type],
            reason="根据用户请求中的任务目标完成确定性路由",
        )

    @staticmethod
    def _heuristic_route(message: str, explicit: str) -> str:
        if explicit != "auto":
            return explicit
        if any(word in message for word in ("匹配", "推荐", "适合", "需求")):
            return "matching"
        if any(word in message for word in ("评估", "评价", "打分", "成熟度")):
            return "evaluation"
        if any(word in message for word in ("搜索", "查找", "找一下", "成果发现", "联网")):
            return "research"
        if any(word in message for word in ("整理需求", "需求规范", "需求诊断")):
            return "requirement_analysis"
        return "qa"


class RequirementAnalystAgent:
    name = "requirement_analyst"

    async def analyze(self, state: AgentState, deps: AgentDependencies) -> RequirementProfile:
        message = state["user_message"]
        if deps.model.configured:
            generated = await safe_structured(
                RequirementProfile,
                REQUIREMENT_ANALYST_SYSTEM_PROMPT,
                message,
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return generated

        scenario_terms = [
            "灌区",
            "渠道",
            "水库",
            "大坝",
            "泵站",
            "河道",
            "水闸",
            "供水",
            "污水",
            "水生态",
            "防洪",
            "监测",
        ]
        scenario = "、".join(term for term in scenario_terms if term in message) or "水利工程"
        hard_constraints: dict[str, Any] = {}
        soft_preferences: dict[str, Any] = {}
        if any(word in message for word in ("预算有限", "低成本", "经费有限")):
            soft_preferences["cost"] = "low"
        if any(word in message for word in ("不停水", "减少停水", "短停水")):
            hard_constraints["construction_impact"] = "minimal_shutdown"
        if "高寒" in message:
            hard_constraints["climate"] = "cold_region"
        if "农村" in message:
            hard_constraints["service_area"] = "rural"

        missing: list[str] = []
        if len(message.strip()) < 12:
            missing.append("工程场景和问题描述")
        completeness = min(0.95, 0.45 + len(message) / 200 + len(hard_constraints) * 0.1)
        requires = bool(missing)
        return RequirementProfile(
            generation_mode="deterministic_fallback",
            title=(message[:36] + "…") if len(message) > 36 else message,
            normalized_description=message.strip(),
            scenario=scenario,
            problem=message.strip(),
            objectives=["寻找可实施、证据充分的水利科技成果"],
            hard_constraints=hard_constraints,
            soft_preferences=soft_preferences,
            known_conditions={},
            missing_critical_fields=missing,
            search_terms=[term for term in scenario_terms if term in message][:5]
            or ["水利科技成果"],
            completeness_score=round(completeness, 2),
            requires_clarification=requires,
            clarification_question=(
                "请补充具体工程对象、当前问题以及不能违反的施工或预算条件。" if requires else ""
            ),
        )


class ResearchAgent:
    name = "research_agent"
    focus_terms: ClassVar[tuple[str, ...]] = ResearchRankingService.focus_terms
    trusted_domains: ClassVar[frozenset[str]] = ResearchRankingService.trusted_domains

    @classmethod
    def _authority_score(cls, url: str) -> tuple[float, str]:
        return ResearchRankingService.authority_score(url)

    @classmethod
    async def _rank_results(
        cls,
        results: list[dict[str, Any]],
        user_request: str,
    ) -> list[dict[str, Any]]:
        return await ResearchRankingService.rank(results, user_request)

    @staticmethod
    def _normalize_queries(queries: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for query in queries:
            compact = " ".join(str(query).split())[:1_500]
            key = compact.casefold()
            if not compact or key in seen:
                continue
            seen.add(key)
            normalized.append(compact)
        return normalized[:6]

    @classmethod
    def _fallback_query_core(cls, user_message: str) -> str:
        active_focus = [term for term in cls.focus_terms if term in user_message]
        date_match = re.search(
            r"20\d{2}\s*年?(?:以来|\s*(?:至|到|[-—~～])\s*20\d{2}\s*年?)",
            user_message,
        )
        parts = [date_match.group(0).replace(" ", "") if date_match else "", *active_focus]
        compact = " ".join(dict.fromkeys(part for part in parts if part))
        if compact:
            return compact
        first_clause = re.split(r"[，。；;]", user_message, maxsplit=1)[0]
        first_clause = re.sub(r"^(?:请|帮我|请帮我)?(?:调研|查找|搜索|找一下)", "", first_clause)
        return " ".join(first_clause.split())[:80] or "水利科技成果"

    async def plan(self, state: AgentState, deps: AgentDependencies) -> ResearchPlan:
        requirement = state.get("requirement_profile", {})
        user_message = state["user_message"]
        if deps.model.configured:
            generated = await safe_structured(
                ResearchPlan,
                RESEARCH_AGENT_SYSTEM_PROMPT,
                json.dumps(
                    {"user_message": user_message, "requirement": requirement},
                    ensure_ascii=False,
                ),
                deps,
                state,
                self.name,
            )
            if generated is not None:
                normalized = self._normalize_queries(generated.queries)
                if normalized:
                    return generated.model_copy(update={"queries": normalized})
        base = " ".join(requirement.get("search_terms", [])) or self._fallback_query_core(
            user_message
        )
        queries = [
            f"水利科技成果 {base} 技术原理 关键指标",
            f"水利 {base} 工程应用 示范 验收 效果",
            f"水利部 水利厅 {base} 科技成果 推广",
            "water pumping station predictive maintenance intelligent scheduling "
            "research paper case study",
        ]
        return ResearchPlan(
            queries=queries,
            target_information=[
                "成果名称",
                "完成单位",
                "技术原理",
                "应用场景",
                "工程案例",
                "关键指标",
                "限制条件",
            ],
        )

    async def research(
        self,
        state: AgentState,
        deps: AgentDependencies,
    ) -> tuple[ResearchPlan, list[dict[str, Any]]]:
        plan = await self.plan(state, deps)
        if not deps.services.search.configured:
            return plan, []
        queries = self._normalize_queries(plan.queries)
        if not queries:
            return plan, []
        start_date, end_date, _ = requested_date_range(state["user_message"])
        semaphore = asyncio.Semaphore(settings.agent_max_parallel_tools)

        async def search_one(
            query_index: int,
            query: str,
        ) -> tuple[list[dict[str, Any]], Exception | None]:
            idempotency_key = sha256(
                f"{state['run_id']}:tavily.search:{query}".encode()
            ).hexdigest()
            async with semaphore:
                tool_call_id, started_at = await deps.services.trace.tool_started(
                    state["run_id"],
                    "tavily.search",
                    idempotency_key,
                    {
                        "query": query,
                        "max_results": 6,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )
                await deps.services.events.publish(
                    state["run_id"],
                    "tool.started",
                    {"tool": "tavily.search", "query": query},
                    agent=self.name,
                    step=self.name,
                )
                try:
                    response = await deps.services.search.search(
                        query,
                        max_results=6,
                        chunks_per_source=3,
                        start_date=start_date,
                        end_date=end_date,
                    )
                except Exception as exc:
                    await deps.services.trace.tool_failed(tool_call_id, started_at, exc)
                    await deps.services.events.publish(
                        state["run_id"],
                        "tool.failed",
                        {
                            "tool": "tavily.search",
                            "query": query,
                            "error": str(exc),
                        },
                        agent=self.name,
                        step=self.name,
                    )
                    return [], exc
                await deps.services.trace.tool_completed(
                    tool_call_id,
                    started_at,
                    {"result_count": len(response.results)},
                )
                await deps.services.events.publish(
                    state["run_id"],
                    "tool.completed",
                    {
                        "tool": "tavily.search",
                        "query": query,
                        "result_count": len(response.results),
                    },
                    agent=self.name,
                    step=self.name,
                )
                return [
                    {
                        "query": query,
                        "query_index": query_index,
                        "query_rank": rank,
                        "title": item.title,
                        "url": str(item.url),
                        "snippet": item.content,
                        "score": item.score,
                    }
                    for rank, item in enumerate(response.results, start=1)
                ], None

        batches = await asyncio.gather(
            *(search_one(index, query) for index, query in enumerate(queries, start=1))
        )
        results = [item for batch, _ in batches for item in batch]
        failures = [failure for _, failure in batches if failure is not None]
        if not results and failures:
            raise failures[0]
        results = await ResearchRankingService.rank(
            results,
            state["user_message"],
            query_count=len(queries),
        )
        if results:
            urls = [item["url"] for item in results[:10]]
            idempotency_key = sha256(
                (f"{state['run_id']}:tavily.extract:" + "|".join(sorted(urls))).encode()
            ).hexdigest()
            tool_call_id, started_at = await deps.services.trace.tool_started(
                state["run_id"],
                "tavily.extract",
                idempotency_key,
                {"urls": urls, "query": state["user_message"]},
            )
            await deps.services.events.publish(
                state["run_id"],
                "tool.started",
                {"tool": "tavily.extract", "url_count": len(urls)},
                agent=self.name,
                step=self.name,
            )
            try:
                extracted = await deps.services.search.extract(
                    urls,
                    query=state["user_message"],
                    chunks_per_source=3,
                )
            except Exception as exc:
                await deps.services.trace.tool_failed(tool_call_id, started_at, exc)
                await deps.services.events.publish(
                    state["run_id"],
                    "tool.failed",
                    {"tool": "tavily.extract", "error": str(exc)},
                    agent=self.name,
                    step=self.name,
                )
                extracted = {"results": [], "failed_results": urls}
            else:
                await deps.services.trace.tool_completed(
                    tool_call_id,
                    started_at,
                    {
                        "result_count": len(extracted.get("results", [])),
                        "failed_count": len(extracted.get("failed_results", [])),
                    },
                )
                await deps.services.events.publish(
                    state["run_id"],
                    "tool.completed",
                    {
                        "tool": "tavily.extract",
                        "result_count": len(extracted.get("results", [])),
                        "failed_count": len(extracted.get("failed_results", [])),
                    },
                    agent=self.name,
                    step=self.name,
                )
            content_by_url = {
                canonicalize_url(str(item.get("url"))): item.get("raw_content", "")
                for item in extracted.get("results", [])
            }
            for item in results:
                item["content"] = content_by_url.get(
                    canonicalize_url(str(item["url"])),
                    item["snippet"],
                )
        return plan, results[:12]


class EvidenceCuratorAgent:
    name = "evidence_curator"

    async def curate(
        self,
        state: AgentState,
        deps: AgentDependencies,
    ) -> CuratedAchievementBatch:
        results = state.get("search_results", [])
        if deps.model.configured and results:
            compact_sources = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "authority_level": item.get("authority_level", "unknown"),
                    "authority_score": item.get("authority_score", 0),
                    "relevance_score": item.get("relevance_score", 0),
                    "query_coverage": item.get("query_coverage", 0),
                    "temporal_status": item.get("temporal_status", "not_requested"),
                    "marketing_signals": item.get("marketing_signals", []),
                    "low_evidence_signals": item.get("low_evidence_signals", []),
                    "source_type": item.get("source_type", "web_source"),
                    # Keep the evidence request within a predictable latency and
                    # context budget. The snippet carries the focused match and
                    # extracted text supplies enough surrounding evidence.
                    "content": (
                        f"搜索摘要：{item.get('snippet', '')}\n"
                        f"来源正文：{item.get('content') or ''}"
                    )[:2_800],
                }
                for item in results[:5]
            ]
            generated = await safe_structured(
                CuratedAchievementBatch,
                EVIDENCE_CURATOR_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "user_request": state["user_message"],
                        "sources": compact_sources,
                    },
                    ensure_ascii=False,
                ),
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return generated

        achievements: list[CuratedAchievement] = []
        rejected_sources: list[dict[str, str]] = []
        for item in results:
            if len(achievements) >= 8:
                break
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet") or "").strip()
            extracted = str(item.get("content") or "").strip()
            content = "\n".join(part for part in (snippet, extracted) if part).strip()
            url = str(item.get("url", ""))
            if not title or not url:
                continue
            if item.get("temporal_status") == "out_of_range":
                rejected_sources.append({"url": url, "reason": "来源明确早于用户要求的时间范围"})
                continue
            if item.get("temporal_status") == "unknown":
                rejected_sources.append(
                    {"url": url, "reason": "来源发布时间无法核实，不满足明确时间约束"}
                )
                continue
            post_extract_marketing_signals = [
                term
                for term in ResearchRankingService.marketing_terms
                if term.casefold() in content.casefold()
            ]
            effective_marketing_signals = list(
                dict.fromkeys(
                    [
                        *[str(term) for term in item.get("marketing_signals", [])],
                        *post_extract_marketing_signals,
                    ]
                )
            )
            if effective_marketing_signals and item.get("authority_level") == "general_web":
                rejected_sources.append(
                    {"url": url, "reason": "普通商业营销页不能单独支撑高置信度成果结论"}
                )
                continue
            if item.get("low_evidence_signals"):
                rejected_sources.append(
                    {"url": url, "reason": "文档主要用途不是描述科技成果或工程证据"}
                )
                continue
            if item.get("focus_coverage") == 0:
                rejected_sources.append(
                    {"url": url, "reason": "可用摘要与正文未覆盖用户要求的核心技术主题"}
                )
                continue
            if item.get("source_type") == "web_source" and (
                item.get("authority_level") == "general_web"
                or not item.get("title_matched_focus_terms")
            ):
                rejected_sources.append(
                    {"url": url, "reason": "通用网页不足以作为可核实科技成果的核心证据"}
                )
                continue
            matched_focus_terms = [str(term) for term in item.get("matched_focus_terms", [])]
            excerpt_focus_terms = [
                alias
                for term in matched_focus_terms
                for alias in (term, *ResearchRankingService.focus_aliases.get(term, ()))
            ]
            excerpt = focused_excerpt(content, excerpt_focus_terms)
            evidence = []
            if excerpt:
                evidence.append(
                    EvidenceClaim(
                        field="abstract",
                        claim=excerpt[:300],
                        evidence_text=excerpt[:500],
                        source_url=url,
                        support_type="direct",
                        confidence=0.6,
                    )
                )
            achievements.append(
                CuratedAchievement(
                    title=title,
                    normalized_title=re.sub(r"[\s《》“”\"'（）()]+", "", title).casefold(),
                    abstract=excerpt,
                    evidence=evidence,
                    source_urls=[url],
                    evidence_score=0.55 if content else 0.2,
                )
            )
        return CuratedAchievementBatch(
            achievements=achievements,
            rejected_sources=rejected_sources,
        )


class AchievementEvaluationAgent:
    name = "achievement_evaluation_agent"

    async def evaluate(
        self,
        state: AgentState,
        deps: AgentDependencies,
    ) -> IntrinsicAchievementEvaluation:
        achievement_id = str(state.get("context", {}).get("achievement_id", ""))
        achievement = await deps.session.get(Achievement, achievement_id)
        if achievement is None:
            raise ValueError("待评价成果不存在")
        payload = {
            "id": achievement.id,
            "title": achievement.title,
            "abstract": achievement.abstract[:500],
            "technical_principle": achievement.technical_principle[:500],
            "application_scenarios": achievement.application_scenarios[:5],
            "maturity_level": achievement.maturity_level,
            "cost_description": achievement.cost_description[:300],
            "advantages": achievement.advantages[:4],
            "limitations": achievement.limitations[:4],
            "metrics": achievement.metrics[:5],
            "tags": achievement.tags[:8],
            "evidence_score": achievement.evidence_score,
            "status": achievement.status,
        }
        missing = []
        if achievement.maturity_level is None:
            missing.append("原始资料未给出明确 TRL，成熟度未知")
        if not achievement.metrics:
            missing.append("缺少可核验的量化技术指标")
        if not achievement.cost_description:
            missing.append("缺少成本、建设与运维投入说明")
        if achievement.evidence_score < 0.7:
            missing.append("高权威来源和工程案例证据仍不充分")
        if deps.model.configured:
            generated = await safe_structured(
                IntrinsicScorecard,
                ACHIEVEMENT_EVALUATION_SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False)[:60_000],
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return IntrinsicAchievementEvaluation(
                    generation_mode="llm",
                    overall_score=generated.overall_score,
                    level=generated.level,
                    dimension_scores={
                        "innovation": generated.innovation,
                        "effectiveness": generated.effectiveness,
                        "maturity": generated.maturity,
                        "evidence_quality": generated.evidence_quality,
                        "promotion_value": generated.promotion_value,
                        "implementation_feasibility": generated.implementation_feasibility,
                    },
                    dimension_details={
                        "innovation": "结合技术原理、已列优势和同类方案差异进行评分。",
                        "effectiveness": "结合量化指标和成果摘要中的实际效果进行评分。",
                        "maturity": (
                            f"结合当前 TRL {achievement.maturity_level or '未知'} "
                            "与工程应用信息评分。"
                        ),
                        "evidence_quality": "结合证据质量分、指标和可核验资料完整度评分。",
                        "promotion_value": "结合适用场景范围、复制条件和成熟度评分。",
                        "implementation_feasibility": "结合成本资料、限制条件和工程成熟度评分。",
                    },
                    summary=generated.summary,
                    strengths=achievement.advantages[:5] or ["成果已形成结构化技术资料"],
                    risks=achievement.limitations[:5] or ["正式工程应用前仍需结合现场条件复核"],
                    missing_information=missing,
                    recommendations=[
                        "针对缺失信息补充第三方检测、工程案例和成本数据"
                        if missing
                        else "保持工程案例和长期运行数据的持续更新"
                    ],
                    evidence=[
                        {"field": "metrics", "items": achievement.metrics[:8]},
                        {"field": "evidence_score", "value": achievement.evidence_score},
                    ],
                    confidence=generated.confidence,
                )

        maturity = float(achievement.maturity_level or 0) / 9 * 100
        evidence_quality = max(0.0, min(100.0, achievement.evidence_score * 100))
        innovation = min(
            90.0,
            58 + len(achievement.advantages) * 4 + bool(achievement.technical_principle) * 8,
        )
        effectiveness = min(
            92.0,
            55 + len(achievement.metrics) * 4 + bool(achievement.abstract) * 6,
        )
        promotion = min(
            90.0,
            52 + len(achievement.application_scenarios) * 5 + maturity * 0.12,
        )
        feasibility = min(88.0, 48 + maturity * 0.28 + bool(achievement.cost_description) * 8)
        dimensions = {
            "innovation": round(innovation, 2),
            "effectiveness": round(effectiveness, 2),
            "maturity": round(maturity, 2),
            "evidence_quality": round(evidence_quality, 2),
            "promotion_value": round(promotion, 2),
            "implementation_feasibility": round(feasibility, 2),
        }
        total = (
            innovation * 0.20
            + effectiveness * 0.20
            + maturity * 0.15
            + evidence_quality * 0.20
            + promotion * 0.15
            + feasibility * 0.10
        )
        if total >= 85:
            level = "优秀"
        elif total >= 70:
            level = "良好"
        elif total >= 60:
            level = "中等"
        else:
            level = "待完善"
        return IntrinsicAchievementEvaluation(
            generation_mode="deterministic_fallback",
            overall_score=round(total, 2),
            level=level,
            dimension_scores=dimensions,
            dimension_details={
                "innovation": "依据技术原理和已列明的技术优势进行保守评分。",
                "effectiveness": "依据量化指标和成果摘要中的效果描述评分。",
                "maturity": (
                    f"依据明确 TRL {achievement.maturity_level} 折算。"
                    if achievement.maturity_level is not None
                    else "TRL 未知，成熟度维度暂不计分。"
                ),
                "evidence_quality": "依据成果证据质量分直接折算。",
                "promotion_value": "依据应用场景覆盖范围和成熟度评分。",
                "implementation_feasibility": "依据成熟度、成本资料和限制条件评分。",
            },
            summary="已完成成果自身质量的六维独立评价。",
            strengths=achievement.advantages[:4] or ["成果已形成结构化技术资料"],
            risks=achievement.limitations[:4] or ["自动评价不能替代工程现场论证"],
            missing_information=missing,
            recommendations=["补充第三方检测、工程案例和成本数据后重新评价"],
            evidence=[{"field": "evidence_score", "value": achievement.evidence_score}],
            confidence=max(0.35, min(0.9, 0.45 + achievement.evidence_score * 0.45)),
        )


class AssessmentMatchingAgent:
    name = "assessment_matching_agent"
    score_weights: ClassVar[dict[str, float]] = {
        "scenario_fit": 0.30,
        "effectiveness": 0.20,
        "maturity": 0.15,
        "evidence_quality": 0.15,
        "cost_fit": 0.10,
        "implementation_readiness": 0.10,
    }
    scenario_terms = (
        "农村",
        "供水",
        "饮水",
        "泵站",
        "监测",
        "智能",
        "信息",
        "高寒",
        "低维护",
        "不停水",
        "水库",
        "大坝",
        "水闸",
        "灌溉",
        "灌区",
        "防洪",
        "生态",
        "混凝土",
        "防渗",
        "污水",
        "水处理",
        "节水",
        "调度",
    )

    @classmethod
    def calculate_final_score(
        cls,
        *,
        scenario_fit: float,
        effectiveness: float,
        maturity: float,
        evidence_quality: float,
        cost_fit: float,
        implementation_risk: float,
    ) -> float:
        """Derive the displayed score from the six explainable dimensions.

        The model may judge individual dimensions, but it must not invent a
        separate headline score that users cannot reproduce from the UI.
        """
        score = (
            scenario_fit * cls.score_weights["scenario_fit"]
            + effectiveness * cls.score_weights["effectiveness"]
            + maturity * cls.score_weights["maturity"]
            + evidence_quality * cls.score_weights["evidence_quality"]
            + cost_fit * cls.score_weights["cost_fit"]
            + (100 - implementation_risk) * cls.score_weights["implementation_readiness"]
        )
        return round(max(0.0, min(100.0, score)), 2)

    @classmethod
    def normalize_batch(cls, batch: MatchAssessmentBatch) -> MatchAssessmentBatch:
        normalized = []
        for item in batch.results:
            normalized.append(
                item.model_copy(
                    update={
                        "final_score": cls.calculate_final_score(
                            scenario_fit=item.scenario_fit,
                            effectiveness=item.effectiveness,
                            maturity=item.maturity,
                            evidence_quality=item.evidence_quality,
                            cost_fit=item.cost_fit,
                            implementation_risk=item.implementation_risk,
                        )
                    }
                )
            )
        normalized.sort(key=lambda item: item.final_score, reverse=True)
        normalized = [
            item.model_copy(update={"rank": rank}) for rank, item in enumerate(normalized, start=1)
        ]
        return batch.model_copy(update={"results": normalized})

    async def match(
        self,
        state: AgentState,
        deps: AgentDependencies,
    ) -> tuple[list[dict[str, Any]], MatchAssessmentBatch]:
        requirement = state.get("requirement_profile", {})
        query = requirement.get("normalized_description") or state["user_message"]
        hits = await deps.services.retrieval.search(
            deps.session,
            query,
            organization_id=state.get("organization_id"),
            constraints=requirement.get("hard_constraints", {}),
            limit=10,
        )
        hit_payloads = [hit.model_dump() for hit in hits]

        if deps.model.configured and hits:
            generated = await safe_structured(
                MatchAssessmentBatch,
                ASSESSMENT_AGENT_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "requirement": requirement,
                        "candidates": hit_payloads[:6],
                    },
                    ensure_ascii=False,
                ),
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return hit_payloads, self.normalize_batch(generated)

        assessments = []
        for rank, hit in enumerate(hits, start=1):
            candidate_text = " ".join(
                [
                    hit.title,
                    hit.abstract,
                    " ".join(str(item) for item in hit.payload.get("tags", [])),
                    " ".join(str(item) for item in hit.payload.get("application_scenarios", [])),
                ]
            )
            primary_text = " ".join(
                [
                    hit.title,
                    " ".join(str(item) for item in hit.payload.get("tags", [])),
                    hit.abstract[:600],
                ]
            )
            classification_text = " ".join(
                [
                    hit.title,
                    " ".join(str(item) for item in hit.payload.get("tags", [])),
                ]
            )
            requested_terms = [term for term in self.scenario_terms if term in str(query)]
            matched_terms = [term for term in requested_terms if term in candidate_text]
            scenario_coverage = (
                len(matched_terms) / len(requested_terms) if requested_terms else 0.5
            )
            primary_coverage = (
                sum(term in primary_text for term in requested_terms) / len(requested_terms)
                if requested_terms
                else 0.5
            )
            semantic_signal = min(1.0, hit.reranker_score * 8)
            relevance = max(
                0,
                min(
                    100,
                    (primary_coverage * 0.45 + scenario_coverage * 0.35 + semantic_signal * 0.20)
                    * 100,
                ),
            )
            evidence_quality = 70 if hit.payload.get("status") == "approved" else 45
            maturity_level = int(hit.payload.get("maturity_level") or 5)
            maturity = maturity_level / 9 * 100
            scenario_fit = relevance
            effectiveness = min(95, 55 + relevance * 0.4)
            cost_fit = 60
            risk_score = max(20, 80 - maturity * 0.5)
            business_adjustment = 0.0
            if "农村" in str(query):
                business_adjustment += 8 if "农村" in classification_text else -4
            if "供水" in str(query):
                business_adjustment += (
                    15
                    if any(term in classification_text for term in ("供水", "饮水", "水处理"))
                    else -18
                )
            if "泵站" in str(query) and "泵站" in classification_text:
                business_adjustment += 6
            if "监测" in str(query) and any(
                term in classification_text for term in ("监测", "信息", "智能")
            ):
                business_adjustment += 7
            scenario_fit = max(0.0, min(100.0, scenario_fit + business_adjustment))
            final_score = self.calculate_final_score(
                scenario_fit=scenario_fit,
                effectiveness=effectiveness,
                maturity=maturity,
                evidence_quality=evidence_quality,
                cost_fit=cost_fit,
                implementation_risk=risk_score,
            )
            fundamental_gaps = []
            if "供水" in str(query) and not any(
                term in classification_text for term in ("供水", "饮水", "水处理")
            ):
                fundamental_gaps.append("成果资料未直接覆盖供水或饮水场景")
            if "监测" in str(query) and not any(
                term in candidate_text for term in ("监测", "信息", "智能")
            ):
                fundamental_gaps.append("成果资料未说明智能监测能力")
            missing_terms = [term for term in requested_terms if term not in matched_terms]
            assessments.append(
                MatchAssessment(
                    achievement_id=hit.achievement_id,
                    title=hit.title,
                    rank=rank,
                    final_score=round(final_score, 2),
                    scenario_fit=round(scenario_fit, 2),
                    effectiveness=round(effectiveness, 2),
                    maturity=round(maturity, 2),
                    evidence_quality=evidence_quality,
                    cost_fit=cost_fit,
                    implementation_risk=round(risk_score, 2),
                    reasons=[
                        (
                            f"覆盖需求场景词：{'、'.join(matched_terms)}"
                            if matched_terms
                            else "与需求存在语义检索相关性"
                        ),
                        f"成果成熟度为 TRL {maturity_level}",
                    ],
                    gaps=[
                        *fundamental_gaps,
                        (
                            f"尚未从资料中确认：{'、'.join(missing_terms[:5])}"
                            if missing_terms
                            else "成本和现场施工条件仍需项目级核实"
                        ),
                    ],
                    risks=["自动评分不能替代工程专家现场论证"],
                )
            )
        assessments.sort(key=lambda item: item.final_score, reverse=True)
        if not assessments:
            for rank, item in enumerate(state.get("evidence_records", [])[:10], start=1):
                evidence_score = float(item.get("evidence_score", 0))
                scenario_fit = min(80.0, 45.0 + evidence_score * 35)
                assessments.append(
                    MatchAssessment(
                        achievement_id=f"candidate:{rank}",
                        title=str(item.get("title", "外部候选成果")),
                        rank=rank,
                        final_score=self.calculate_final_score(
                            scenario_fit=scenario_fit,
                            effectiveness=50,
                            maturity=40,
                            evidence_quality=evidence_score * 100,
                            cost_fit=50,
                            implementation_risk=65,
                        ),
                        scenario_fit=scenario_fit,
                        effectiveness=50,
                        maturity=40,
                        evidence_quality=evidence_score * 100,
                        cost_fit=50,
                        implementation_risk=65,
                        reasons=["来自 Tavily 搜索的外部候选，已完成初步证据整理"],
                        gaps=["尚未进入正式成果库，需人工审核和工程条件核实"],
                        risks=["外部候选资料可能不完整"],
                        cited_source_ids=list(item.get("source_urls", [])),
                    )
                )
        for rank, item in enumerate(assessments, start=1):
            item.rank = rank
        return hit_payloads, MatchAssessmentBatch(
            generation_mode="deterministic_fallback",
            results=assessments,
            overall_summary="候选成果已完成混合检索与确定性多维评分。",
        )


class VerificationAgent:
    name = "verification_agent"

    async def verify(self, state: AgentState, deps: AgentDependencies) -> VerificationResult:
        evaluation = state.get("achievement_evaluation", {})
        if state.get("task_type") == "evaluation" and evaluation:
            required_dimensions = {
                "innovation",
                "effectiveness",
                "maturity",
                "evidence_quality",
                "promotion_value",
                "implementation_feasibility",
            }
            scores = evaluation.get("dimension_scores", {})
            complete = required_dimensions.issubset(scores) and all(
                0 <= float(scores[key]) <= 100 for key in required_dimensions
            )
            return VerificationResult(
                decision="passed" if complete else "human_review_required",
                confidence=float(evaluation.get("confidence", 0.6)),
                unsupported_claims=[] if complete else ["成果评价维度不完整"],
                recommended_action=(
                    "六维评分结构完整，可进入成果初筛和专家复核"
                    if complete
                    else "请由专家补齐评价维度"
                ),
                review_reason="评价结构不完整" if not complete else "",
            )
        provider_degraded = (
            evaluation.get("generation_mode") == "deterministic_fallback"
            or state.get("requirement_profile", {}).get("generation_mode")
            == "deterministic_fallback"
            or state.get("assessment_generation_mode") == "deterministic_fallback"
        )
        if provider_degraded:
            return VerificationResult(
                decision="passed",
                confidence=float(evaluation.get("confidence", 0.6)),
                recommended_action="模型服务暂时不可用，已完成保守评分；建议恢复后重新评价",
            )
        payload = {
            "user_request": state["user_message"],
            "task_type": state.get("task_type"),
            "requirement": state.get("requirement_profile", {}),
            "sources": state.get("search_results", []),
            "evidence": state.get("evidence_records", []),
            "rejected_sources": state.get("rejected_sources", []),
            "assessments": state.get("assessment_results", []),
            "achievement_evaluation": state.get("achievement_evaluation", {}),
        }
        if deps.model.configured:
            generated = await safe_structured(
                VerificationResult,
                VERIFICATION_AGENT_SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False)[:60_000],
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return generated

        assessments = state.get("assessment_results", [])
        evidence = state.get("evidence_records", [])
        task_type = state.get("task_type")
        evaluation = state.get("achievement_evaluation", {})
        if task_type in {"matching", "evaluation"} and not assessments:
            return VerificationResult(
                decision="human_review_required",
                confidence=0.2,
                unsupported_claims=["没有足够的已审核成果支撑结果"],
                recommended_action="补充成果资料或启用 Tavily 搜索",
                review_reason="候选成果不足",
            )
        if task_type == "research" and not evidence:
            return VerificationResult(
                decision="rejected",
                confidence=0.1,
                unsupported_claims=["没有获得可核验来源"],
                recommended_action="检查 Tavily 配置后重新搜索",
            )
        return VerificationResult(
            decision="passed",
            confidence=0.72,
            recommended_action="结果可用于初步筛选，正式工程决策仍需专家复核",
        )


class FinalReportAgent:
    name = "final_report_agent"

    async def render(self, state: AgentState, deps: AgentDependencies) -> str:
        numbered_sources = [
            {
                "number": index,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "authority_level": item.get("authority_level", "unknown"),
            }
            for index, item in enumerate(accepted_search_results(state), start=1)
        ]
        payload = {
            "user_request": state["user_message"],
            "task_type": state.get("task_type"),
            "requirement": state.get("requirement_profile", {}),
            "evidence": state.get("evidence_records", []),
            "rejected_sources": state.get("rejected_sources", []),
            "assessments": state.get("assessment_results", []),
            "achievement_evaluation": state.get("achievement_evaluation", {}),
            "verification": state.get("verification", {}),
            "human_decision": state.get("human_decision", {}),
            "research_ingestion": state.get("ingestion_summary", {}),
            "ingested_achievement_ids": state.get("ingested_achievement_ids", []),
            "sources": numbered_sources,
        }
        provider_degraded = (
            state.get("achievement_evaluation", {}).get("generation_mode")
            == "deterministic_fallback"
            or state.get("requirement_profile", {}).get("generation_mode")
            == "deterministic_fallback"
            or state.get("assessment_generation_mode") == "deterministic_fallback"
        )
        if deps.model.configured and not provider_degraded:
            generated = await safe_text(
                FINAL_REPORT_SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False)[:60_000],
                deps,
                state,
                self.name,
            )
            if generated is not None:
                return generated

        lines = ["## HydroBridge AI 分析结果", ""]
        requirement = state.get("requirement_profile")
        if requirement:
            lines.extend(
                [
                    "### 需求理解",
                    "",
                    requirement.get("normalized_description", state["user_message"]),
                    "",
                ]
            )
        assessments = state.get("assessment_results", [])
        achievement_evaluation = state.get("achievement_evaluation", {})
        if achievement_evaluation:
            lines.extend(
                [
                    "### 成果综合评价",
                    "",
                    f"- 综合得分：{float(achievement_evaluation.get('overall_score', 0)):.1f}",
                    f"- 评价等级：{achievement_evaluation.get('level', '待确认')}",
                    f"- 评价摘要：{achievement_evaluation.get('summary', '')}",
                    "",
                ]
            )
        if assessments:
            lines.extend(["### 推荐成果", ""])
            for item in assessments[:5]:
                lines.append(
                    f"{item['rank']}. **{item['title']}** — 综合得分 {item['final_score']:.1f}"
                )
                for reason in item.get("reasons", [])[:3]:
                    lines.append(f"   - {reason}")
                for risk in item.get("risks", [])[:2]:
                    lines.append(f"   - 风险：{risk}")
            lines.append("")
        evidence = state.get("evidence_records", [])
        if evidence and not assessments:
            lines.extend(["### 发现的候选成果", ""])
            for index, item in enumerate(evidence[:8], start=1):
                source = (item.get("source_urls") or [""])[0]
                lines.append(f"{index}. **{item.get('title', '未命名成果')}**")
                if item.get("abstract"):
                    lines.append(f"   - {item['abstract'][:180]}")
                if source:
                    lines.append(f"   - 来源：{source}")
            lines.append("")
        ingestion = state.get("ingestion_summary", {})
        if ingestion:
            lines.extend(
                [
                    "### 成果入库分流",
                    "",
                    f"- 高置信度自动入库：{ingestion.get('auto_approved', 0)} 项",
                    f"- 人工确认后入库：{ingestion.get('human_approved', 0)} 项",
                    f"- 自动淘汰：{ingestion.get('rejected', 0)} 项",
                    f"- 暂存线索：{ingestion.get('deferred', 0)} 项",
                    "",
                ]
            )
        verification = state.get("verification", {})
        lines.extend(
            [
                "### 核验结论",
                "",
                f"- 状态：{verification.get('decision', '未核验')}",
                f"- 置信度：{float(verification.get('confidence', 0)):.0%}",
                f"- 建议：{verification.get('recommended_action', '请结合工程现场复核')}",
            ]
        )
        return "\n".join(lines)


def source_authority(url: str) -> tuple[str, float]:
    hostname = (urlparse(url).hostname or "").lower()
    if hostname.endswith(".gov.cn") or hostname == "mwr.gov.cn":
        return "official", 0.95
    if hostname.endswith(".edu.cn") or "cas.cn" in hostname:
        return "academic", 0.85
    if any(token in hostname for token in ("water", "hydro", "slj")):
        return "industry", 0.7
    return "unknown", 0.45
