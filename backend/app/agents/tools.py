"""Public action contracts, independent of how each business subgraph executes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.requirement_patch import RequirementChanges, merge_requirement


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


ACTION_SCHEMAS = {
    "research_achievements": (MatchArguments, "联网搜寻新成果，提取证据并入库或转人工审核"),
    "evaluate_achievement": (EvaluateArguments, "评价单项成果自身质量；传入已知ID或准确名称"),
    "normalize_requirement": (MatchArguments, "独立整理和澄清需求，本次不执行匹配"),
    "match_achievements": (MatchArguments, "解析需求、检索并核验，返回有序候选和成果ID"),
    "update_requirement": (UpdateArguments, "以完整的新描述更新当前需求，清空旧画像"),
    "search_achievements": (SearchArguments, "检索库内成果并依据原文回答，最多两轮取证"),
    "get_achievement": (DetailArguments, "读取一项有权限成果的结构化概览"),
    "compare_achievements": (CompareArguments, "在指定的2至5项成果内依据原文对比"),
}


class ResearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["research", "evaluate"] = "research"
    query: str = Field(default="", max_length=5000)
    achievement_id: str | None = None
    achievement_rank: int | None = Field(default=None, strict=True, ge=1, le=20)
    title: str | None = Field(default=None, max_length=300)


class RequirementArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["match", "analyze", "refine"] = "match"
    query: str = Field(default="", max_length=10000)
    patch: RequirementChanges | None = None
    match_after_refine: bool = True


class AskArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["search", "detail", "compare"] = "search"
    query: str = Field(default="", max_length=5000)
    achievement_ids: list[str] | None = Field(default=None, max_length=20)
    achievement_ranks: list[Annotated[int, Field(strict=True, ge=1, le=20)]] | None = Field(
        default=None, max_length=20
    )


TOOL_SCHEMAS = {
    "research_achievements": (
        ResearchArguments,
        "成果搜寻与评价：research联网发现和入库；evaluate评价指定的一项成果自身质量",
    ),
    "match_achievements": (
        RequirementArguments,
        "需求分析与匹配：analyze仅整理需求；match分析并匹配；refine局部更新已有画像，"
        "patch只传用户明确修改的字段，未传字段保持不变，可选择是否继续匹配",
    ),
    "ask_achievements": (
        AskArguments,
        "成果答疑问答：search检索原文回答；detail结构化概览；compare对比2至5项。"
        "序号追问传achievement_ranks，由服务端按active_candidates解析；ID与序号不能混传",
    ),
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


def resolve_ranks(state, ranks):
    candidates = {item["rank"]: item["id"] for item in state.get("active_candidates", [])}
    if not ranks or any(
        type(rank) is not int or rank < 1 or rank not in candidates for rank in ranks
    ):
        raise ValueError("候选序号无效或缺少有序候选列表，请先检索或明确成果名称")
    ids = [candidates[rank] for rank in ranks]
    if len(ids) != len(set(ids)):
        raise ValueError("成果序号不能重复")
    scope = achievement_scope(state)
    if scope is not None and any(identity not in scope for identity in ids):
        raise ValueError("指定候选超出当前限定范围，请重新选择范围内的成果")
    return ids


def normalize_public_call(call, state):
    """Three model-facing tools map to stable internal actions/checkpoint node names."""
    name = call.get("name")
    if name not in TOOL_SCHEMAS:
        raise ValueError("未知工具，请使用三个已提供的业务工具")
    args = TOOL_SCHEMAS[name][0].model_validate(call.get("arguments", {}))
    if isinstance(args, ResearchArguments):
        if args.mode == "research":
            if args.achievement_id or args.achievement_rank or args.title:
                raise ValueError("research模式只接收query；评价已有成果请用evaluate")
            action, arguments = name, {"query": args.query}
        else:
            if (
                sum(
                    bool(value)
                    for value in (args.achievement_id, args.achievement_rank, args.title)
                )
                > 1
            ):
                raise ValueError("评价对象请在ID、序号、准确名称中选择一种")
            identity = (
                resolve_ranks(state, [args.achievement_rank])[0]
                if args.achievement_rank is not None
                else args.achievement_id
            )
            action = "evaluate_achievement"
            arguments = {"achievement_id": identity, "title": args.title}
    elif isinstance(args, RequirementArguments):
        if args.mode != "refine" and args.patch is not None:
            raise ValueError("局部修改需求必须使用refine模式")
        action = (
            "normalize_requirement"
            if args.mode == "analyze" or (args.mode == "refine" and not args.match_after_refine)
            else "match_achievements"
        )
        arguments = {"query": args.query}
        if args.mode == "refine":
            if args.patch is None:
                raise ValueError("请提供明确的patch；预算放宽等模糊指令应先询问具体边界")
            # Validate before creating any workflow records. Merge again inside the subgraph.
            merge_requirement(state.get("requirement_profile", {}), args.patch)
            arguments["requirement_patch"] = args.patch.model_dump(exclude_unset=True)
    else:
        if args.achievement_ids is not None and args.achievement_ranks is not None:
            raise ValueError("成果ID与候选序号不能同时传入")
        ids = (
            resolve_ranks(state, args.achievement_ranks)
            if args.achievement_ranks is not None
            else args.achievement_ids
        )
        if ids is not None and len(ids) != len(set(ids)):
            raise ValueError("成果ID不能重复")
        scope = achievement_scope(state)
        if ids is not None and scope is not None and any(identity not in scope for identity in ids):
            raise ValueError("指定成果超出当前限定范围")
        if args.mode == "detail":
            if len(ids or []) != 1:
                raise ValueError("查看详情需要明确指定一项成果")
            action, arguments = "get_achievement", {"achievement_id": ids[0]}
        elif args.mode == "compare":
            action, arguments = (
                "compare_achievements",
                {
                    "achievement_ids": ids,
                    "question": args.query or "比较适用场景、技术指标与限制条件",
                },
            )
        else:
            action, arguments = "search_achievements", {"query": args.query, "achievement_ids": ids}
    validated = ACTION_SCHEMAS[action][0].model_validate(arguments).model_dump()
    if "requirement_patch" in arguments:
        validated["requirement_patch"] = arguments["requirement_patch"]
    return {
        "name": action,
        "arguments": validated,
        "public_call": {"name": name, "arguments": args.model_dump(exclude_unset=True)},
    }


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
