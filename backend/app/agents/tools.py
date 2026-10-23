"""Public action contracts, independent of how each business subgraph executes."""

from pydantic import BaseModel, Field


class SearchArguments(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    achievement_ids: list[str] | None = Field(default=None, max_length=20)


class MatchArguments(BaseModel):
    query: str = Field(min_length=1, max_length=10000)


class DetailArguments(BaseModel):
    achievement_id: str


class CompareArguments(BaseModel):
    achievement_ids: list[str] = Field(min_length=2, max_length=5)
    question: str = Field(default="比较适用场景、技术指标与限制条件", max_length=5000)


class UpdateArguments(BaseModel):
    description: str = Field(min_length=1, max_length=10000)


class EvaluateArguments(BaseModel):
    achievement_id: str | None = None
    title: str | None = Field(default=None, max_length=300)


TOOL_SCHEMAS = {
    "research_achievements": (MatchArguments, "联网搜寻新成果，提取证据并入库或转人工审核"),
    "evaluate_achievement": (EvaluateArguments, "评价单项成果自身质量；传入已知ID或准确名称"),
    "normalize_requirement": (MatchArguments, "独立整理和澄清需求，本次不执行匹配"),
    "match_achievements": (MatchArguments, "解析需求、检索并核验，返回有序候选和成果ID"),
    "update_requirement": (UpdateArguments, "以完整的新描述更新当前需求，清空旧画像"),
    "search_achievements": (SearchArguments, "检索库内成果并依据原文回答，最多两轮取证"),
    "get_achievement": (DetailArguments, "读取一项有权限成果的结构化概览"),
    "compare_achievements": (CompareArguments, "在指定的2至5项成果内依据原文对比"),
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema.model_json_schema(),
        },
    }
    for name, (schema, description) in TOOL_SCHEMAS.items()
]

ACTION_TASKS = {
    "research_achievements": "research",
    "evaluate_achievement": "evaluation",
    "normalize_requirement": "requirement_analysis",
    "match_achievements": "matching",
    "update_requirement": "requirement_analysis",
    "search_achievements": "qa",
    "get_achievement": "qa",
    "compare_achievements": "qa",
}
ACTION_GRAPHS = {
    name: (
        "achievement_pipeline"
        if task in {"research", "evaluation"}
        else "requirement_pipeline"
        if task in {"requirement_analysis", "matching"}
        else "retrieval_qa_pipeline"
    )
    for name, task in ACTION_TASKS.items()
}


def achievement_scope(state):
    """Keep the request's boundary separate from candidates discovered during its actions."""
    if "scope_limit" in state:
        ceiling = state["scope_limit"]
        return list(ceiling) if ceiling is not None else None
    # Also support callers inspecting an initial state before prepare_context runs.
    context = state.get("context", {})
    if "achievement_ids" in context:
        return list(context["achievement_ids"])
    if context.get("achievement_id"):
        return [str(context["achievement_id"])]
    if state.get("scope_is_set") or state.get("selected_achievement_ids"):
        return list(state.get("selected_achievement_ids", []))
    return None
