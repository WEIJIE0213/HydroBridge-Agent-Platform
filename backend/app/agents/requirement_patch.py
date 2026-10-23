"""Deterministic, field-level updates; unchanged constraints never pass through an LLM."""

import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.schemas import RequirementProfile


class RequirementChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    scenario: str | None = Field(default=None, min_length=1, max_length=2000)
    problem: str | None = Field(default=None, min_length=1, max_length=5000)
    objectives: list[str] | None = None
    hard_constraints: dict[str, Any] | None = None
    soft_preferences: dict[str, Any] | None = None
    known_conditions: dict[str, Any] | None = None
    search_terms: list[str] | None = None


def merge_requirement(profile: dict, patch: RequirementChanges) -> dict:
    if (
        not profile.get("normalized_description")
        or profile.get("missing_critical_fields")
        or profile.get("requires_clarification")
    ):
        raise ValueError("请先完成当前需求分析和关键条件澄清，再局部修改需求")
    changes = patch.model_dump(exclude_unset=True)
    if not changes or all(value == {} for value in changes.values()):
        raise ValueError("patch不能为空，请只填写用户明确修改的字段")
    merged = deepcopy(profile)
    for field, value in changes.items():
        if value is None:
            raise ValueError("不能清空整个画像字段；删除约束时将字典中的指定键设为null")
        if field in {"hard_constraints", "soft_preferences", "known_conditions"}:
            target = merged.setdefault(field, {})
            for key, new_value in value.items():
                if not key.strip():
                    raise ValueError("约束字段名不能为空")
                if new_value is None:
                    if key not in target:
                        raise ValueError(f"待删除字段不存在：{field}.{key}")
                    target.pop(key)
                else:
                    # One named constraint is the atomic unit; other keys remain byte-for-byte.
                    target[key] = deepcopy(new_value)
        else:
            merged[field] = deepcopy(value)
    trl = merged.get("hard_constraints", {}).get("min_maturity_level")
    if trl is not None and (type(trl) is not int or not 1 <= trl <= 9):
        raise ValueError("min_maturity_level必须是1至9的整数")
    # Use current structured fields rather than reuse the previous normalized description.
    labels = {
        "scenario": "工程场景",
        "problem": "核心问题",
        "objectives": "目标",
        "hard_constraints": "硬约束",
        "soft_preferences": "软偏好",
        "known_conditions": "已知条件",
    }
    merged["normalized_description"] = "；".join(
        f"{label}：{json.dumps(merged.get(field), ensure_ascii=False)}"
        for field, label in labels.items()
        if merged.get(field)
    )
    merged.update(
        generation_mode="structured_patch", requires_clarification=False, clarification_question=""
    )
    # Validate shape without re-extracting or silently normalizing unrelated constraints.
    RequirementProfile.model_validate(merged)
    return merged


def describe_changes(patch: dict) -> str:
    fields = {
        "title": "需求标题",
        "scenario": "工程场景",
        "problem": "核心问题",
        "objectives": "目标",
        "hard_constraints": "硬约束",
        "soft_preferences": "软偏好",
        "known_conditions": "已知条件",
        "search_terms": "检索词",
    }
    keys = {
        "budget": "预算",
        "cost": "成本",
        "min_maturity_level": "最低成熟度",
        "construction_impact": "施工影响",
        "climate": "气候",
        "service_area": "服务区域",
    }
    changes = []
    for field, value in patch.items():
        items = value.items() if isinstance(value, dict) else [("", value)]
        for key, item in items:
            label = fields[field] + (f"中的{keys.get(key, key)}" if key else "")
            rendered = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            changes.append(f"取消{label}" if item is None else f"{label}更新为{rendered}")
    return "本轮需求修改：" + "；".join(changes) + "。未指定的字段保留。"
