from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.model_gateway import ModelGateway
from app.agents.prompts import (
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
    MatchAssessment,
    MatchAssessmentBatch,
    RequirementProfile,
    ResearchPlan,
    SupervisorDecision,
    VerificationResult,
)
from app.agents.state import AgentState
from app.services.container import ServiceContainer


@dataclass
class AgentDependencies:
    session: AsyncSession
    services: ServiceContainer
    model: ModelGateway


class SupervisorAgent:
    name = "supervisor"

    async def decide(self, state: AgentState, deps: AgentDependencies) -> SupervisorDecision:
        message = state["user_message"]
        explicit = state.get("task_type", "auto")
        if deps.model.configured:
            return await deps.model.structured(
                SupervisorDecision,
                SUPERVISOR_SYSTEM_PROMPT,
                f"显式任务类型：{explicit}\n用户请求：{message}",
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )

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
            return await deps.model.structured(
                RequirementProfile,
                REQUIREMENT_ANALYST_SYSTEM_PROMPT,
                message,
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )

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
                "请补充具体工程对象、当前问题以及不能违反的施工或预算条件。"
                if requires
                else ""
            ),
        )


class ResearchAgent:
    name = "research_agent"

    async def plan(self, state: AgentState, deps: AgentDependencies) -> ResearchPlan:
        requirement = state.get("requirement_profile", {})
        user_message = state["user_message"]
        if deps.model.configured:
            return await deps.model.structured(
                ResearchPlan,
                RESEARCH_AGENT_SYSTEM_PROMPT,
                json.dumps(
                    {"user_message": user_message, "requirement": requirement},
                    ensure_ascii=False,
                ),
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )
        base = " ".join(requirement.get("search_terms", [])) or user_message
        queries = [
            f"水利科技成果 {base} 完成单位 技术简介",
            f"水利 {base} 工程应用 示范 推广",
            f"{base} 技术成果 关键指标 适用场景",
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
        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for query in plan.queries:
            idempotency_key = sha256(
                f"{state['run_id']}:tavily.search:{query}".encode()
            ).hexdigest()
            tool_call_id, started_at = await deps.services.trace.tool_started(
                state["run_id"],
                "tavily.search",
                idempotency_key,
                {"query": query, "max_results": 6},
            )
            await deps.services.events.publish(
                state["run_id"],
                "tool.started",
                {"tool": "tavily.search", "query": query},
                agent=self.name,
                step=self.name,
            )
            try:
                response = await deps.services.search.search(query, max_results=6)
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
                raise
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
            for item in response.results:
                url = str(item.url)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(
                    {
                        "query": query,
                        "title": item.title,
                        "url": url,
                        "snippet": item.content,
                        "score": item.score,
                    }
                )
        if results:
            urls = [item["url"] for item in results[:10]]
            idempotency_key = sha256(
                (
                    f"{state['run_id']}:tavily.extract:"
                    + "|".join(sorted(urls))
                ).encode()
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
                raise
            await deps.services.trace.tool_completed(
                tool_call_id,
                started_at,
                {"result_count": len(extracted.get("results", []))},
            )
            await deps.services.events.publish(
                state["run_id"],
                "tool.completed",
                {
                    "tool": "tavily.extract",
                    "result_count": len(extracted.get("results", [])),
                },
                agent=self.name,
                step=self.name,
            )
            content_by_url = {
                str(item.get("url")): item.get("raw_content", "")
                for item in extracted.get("results", [])
            }
            for item in results:
                item["content"] = content_by_url.get(item["url"], item["snippet"])
        return plan, results[:20]


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
                    "content": str(item.get("content") or item.get("snippet") or "")[:12_000],
                }
                for item in results[:8]
            ]
            return await deps.model.structured(
                CuratedAchievementBatch,
                EVIDENCE_CURATOR_SYSTEM_PROMPT,
                json.dumps(compact_sources, ensure_ascii=False),
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )

        achievements: list[CuratedAchievement] = []
        for item in results[:8]:
            title = str(item.get("title", "")).strip()
            content = str(item.get("content") or item.get("snippet") or "").strip()
            url = str(item.get("url", ""))
            if not title or not url:
                continue
            evidence = []
            if content:
                evidence.append(
                    EvidenceClaim(
                        field="abstract",
                        claim=content[:300],
                        evidence_text=content[:500],
                        source_url=url,
                        support_type="direct",
                        confidence=0.6,
                    )
                )
            achievements.append(
                CuratedAchievement(
                    title=title,
                    normalized_title=re.sub(r"[\s《》“”\"'（）()]+", "", title).casefold(),
                    abstract=content[:800],
                    evidence=evidence,
                    source_urls=[url],
                    evidence_score=0.55 if content else 0.2,
                )
            )
        return CuratedAchievementBatch(achievements=achievements)


class AssessmentMatchingAgent:
    name = "assessment_matching_agent"
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
            return hit_payloads, await deps.model.structured(
                MatchAssessmentBatch,
                ASSESSMENT_AGENT_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "requirement": requirement,
                        "candidates": hit_payloads,
                    },
                    ensure_ascii=False,
                ),
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )

        assessments = []
        for rank, hit in enumerate(hits, start=1):
            candidate_text = " ".join(
                [
                    hit.title,
                    hit.abstract,
                    " ".join(str(item) for item in hit.payload.get("tags", [])),
                    " ".join(
                        str(item)
                        for item in hit.payload.get("application_scenarios", [])
                    ),
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
            requested_terms = [
                term for term in self.scenario_terms if term in str(query)
            ]
            matched_terms = [
                term for term in requested_terms if term in candidate_text
            ]
            scenario_coverage = (
                len(matched_terms) / len(requested_terms)
                if requested_terms
                else 0.5
            )
            primary_coverage = (
                sum(term in primary_text for term in requested_terms)
                / len(requested_terms)
                if requested_terms
                else 0.5
            )
            semantic_signal = min(1.0, hit.reranker_score * 8)
            relevance = max(
                0,
                min(
                    100,
                    (
                        primary_coverage * 0.45
                        + scenario_coverage * 0.35
                        + semantic_signal * 0.20
                    )
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
            final_score = (
                scenario_fit * 0.30
                + effectiveness * 0.20
                + maturity * 0.15
                + evidence_quality * 0.15
                + cost_fit * 0.10
                + (100 - risk_score) * 0.10
            )
            business_adjustment = 0.0
            if "农村" in str(query):
                business_adjustment += (
                    8 if "农村" in classification_text else -4
                )
            if "供水" in str(query):
                business_adjustment += (
                    15
                    if any(
                        term in classification_text
                        for term in ("供水", "饮水", "水处理")
                    )
                    else -18
                )
            if "泵站" in str(query) and "泵站" in classification_text:
                business_adjustment += 6
            if "监测" in str(query) and any(
                term in classification_text for term in ("监测", "信息", "智能")
            ):
                business_adjustment += 7
            final_score = max(0.0, min(100.0, final_score + business_adjustment))
            fundamental_gaps = []
            if "供水" in str(query) and not any(
                term in classification_text for term in ("供水", "饮水", "水处理")
            ):
                fundamental_gaps.append("成果资料未直接覆盖供水或饮水场景")
            if "监测" in str(query) and not any(
                term in candidate_text for term in ("监测", "信息", "智能")
            ):
                fundamental_gaps.append("成果资料未说明智能监测能力")
            missing_terms = [
                term for term in requested_terms if term not in matched_terms
            ]
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
                        final_score=round(40 + evidence_score * 35, 2),
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
            results=assessments,
            overall_summary="候选成果已完成混合检索与确定性多维评分。",
        )


class VerificationAgent:
    name = "verification_agent"

    async def verify(self, state: AgentState, deps: AgentDependencies) -> VerificationResult:
        payload = {
            "task_type": state.get("task_type"),
            "requirement": state.get("requirement_profile", {}),
            "sources": state.get("search_results", []),
            "evidence": state.get("evidence_records", []),
            "assessments": state.get("assessment_results", []),
        }
        if deps.model.configured:
            return await deps.model.structured(
                VerificationResult,
                VERIFICATION_AGENT_SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False)[:60_000],
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )

        assessments = state.get("assessment_results", [])
        evidence = state.get("evidence_records", [])
        task_type = state.get("task_type")
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
        payload = {
            "task_type": state.get("task_type"),
            "requirement": state.get("requirement_profile", {}),
            "evidence": state.get("evidence_records", []),
            "assessments": state.get("assessment_results", []),
            "verification": state.get("verification", {}),
            "human_decision": state.get("human_decision", {}),
        }
        if deps.model.configured:
            return await deps.model.text(
                FINAL_REPORT_SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False)[:60_000],
                session=deps.session,
                run_id=state["run_id"],
                agent_name=self.name,
            )

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
