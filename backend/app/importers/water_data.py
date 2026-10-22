from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.domain import Achievement, AchievementSource, Taxonomy
from app.db.models.knowledge import OutboxEvent
from app.db.session import AsyncSessionFactory

FIELD_TO_TAXONOMY = {
    "防洪": "flood",
    "灌溉": "irrigation",
    "灌区": "irrigation",
    "农村饮水": "water-supply",
    "供水": "water-supply",
    "污水": "water-supply",
    "水处理": "water-supply",
    "生态": "ecology",
    "护坡": "ecology",
    "水库": "hydraulic-structure",
    "大坝": "hydraulic-structure",
    "水闸": "hydraulic-structure",
    "防渗": "hydraulic-structure",
    "混凝土": "hydraulic-structure",
    "监测": "smart-water",
    "信息": "smart-water",
    "调度": "smart-water",
    "节水": "water-resource",
}


def normalize_title(title: str) -> str:
    return re.sub(r"[\s《》“”\"'（）()]+", "", title).casefold()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(filter(None, (_as_text(item) for item in value)))
    if isinstance(value, dict):
        return "；".join(f"{key}：{text}" for key, raw in value.items() if (text := _as_text(raw)))
    return str(value)


def _entity_text(entity: dict[str, Any]) -> str:
    name = str(entity.get("name", "")).strip()
    attributes = _as_text(entity.get("attributes", {}))
    return "；".join(part for part in (name, attributes) if part)


def _entities_by_type(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entity in document.get("entities", []):
        if not isinstance(entity, dict):
            continue
        grouped.setdefault(str(entity.get("type", "其他")), []).append(entity)
    return grouped


def normalize_document(document: dict[str, Any], source_name: str) -> dict[str, Any]:
    grouped = _entities_by_type(document)
    achievement_entities = grouped.get("科技成果", [])
    if not achievement_entities:
        raise ValueError(f"{source_name} 缺少“科技成果”实体")
    root = achievement_entities[0]
    title = str(root.get("name", "")).strip() or Path(source_name).stem
    root_attributes = dict(root.get("attributes") or {})

    problems = grouped.get("待解决问题", [])
    core_technologies = grouped.get("成果的核心技术", [])
    innovations = grouped.get("项目创新程度", [])
    applications = grouped.get("应用实例", [])
    application_ranges = grouped.get("应用范围及前景", [])
    benefits = grouped.get("效益", [])
    difficulties = grouped.get("项目难度与复杂度", [])
    metric_entities = [
        *grouped.get("性能指标", []),
        *grouped.get("经济指标", []),
    ]

    background = _as_text(root_attributes.get("立项背景"))
    problem_text = "\n".join(_entity_text(item) for item in problems)
    abstract = "\n".join(filter(None, (background, problem_text)))[:8_000]
    technical_principle = "\n".join(_entity_text(item) for item in core_technologies)[:20_000]
    application_scenarios = [
        _entity_text(item)[:1_000]
        for item in [*applications, *application_ranges]
        if _entity_text(item)
    ][:20]
    advantages = [
        _entity_text(item)[:1_000] for item in [*innovations, *benefits] if _entity_text(item)
    ][:20]
    limitations = [_entity_text(item)[:1_000] for item in difficulties if _entity_text(item)][:10]
    for problem in problems:
        challenges = (problem.get("attributes") or {}).get("挑战因素", [])
        if isinstance(challenges, list):
            limitations.extend(str(item)[:1_000] for item in challenges if item)

    metrics: list[dict[str, object]] = []
    for item in metric_entities:
        attributes = dict(item.get("attributes") or {})
        metrics.append(
            {
                "name": attributes.get("指标名称") or item.get("name") or "指标",
                "value": (
                    attributes.get("指标值、单位")
                    or attributes.get("指标值")
                    or attributes.get("数值")
                    or _as_text(attributes)
                ),
                "category": item.get("type", "性能指标"),
            }
        )

    organizations_raw = root_attributes.get("完成单位", [])
    organizations = (
        [str(item) for item in organizations_raw if item]
        if isinstance(organizations_raw, list)
        else [str(organizations_raw)]
        if organizations_raw
        else []
    )
    field_text = _as_text(root_attributes.get("所属领域"))
    taxonomy_code = "water-resource"
    full_text = f"{title} {field_text} {abstract}"
    for keyword, code in FIELD_TO_TAXONOMY.items():
        if keyword in full_text:
            taxonomy_code = code
            break

    tags = [item.strip() for item in re.split(r"[,，、;/；\s]+", field_text) if item.strip()]
    if not tags:
        tags = [taxonomy_code]
    evidence_dimensions = sum(
        bool(grouped.get(entity_type))
        for entity_type in (
            "科技成果",
            "成果的核心技术",
            "性能指标",
            "应用实例",
            "项目创新程度",
            "知识产权及衍生品",
        )
    )
    evidence_score = round(min(0.96, 0.45 + evidence_dimensions * 0.075), 2)
    # Application cases do not establish a technology readiness level.
    # Only an explicit, numeric TRL in the supplied source can populate it.
    source_trl = root_attributes.get("TRL", root_attributes.get("技术成熟度等级"))
    maturity_level = (
        source_trl
        if isinstance(source_trl, int) and not isinstance(source_trl, bool) and 1 <= source_trl <= 9
        else None
    )

    return {
        "title": title,
        "normalized_title": normalize_title(title),
        "abstract": abstract,
        "technical_principle": technical_principle,
        "application_scenarios": application_scenarios,
        "organizations": organizations,
        "maturity_level": maturity_level,
        "cost_description": "\n".join(_entity_text(item) for item in grouped.get("经济指标", []))[
            :8_000
        ],
        "advantages": advantages,
        "limitations": limitations[:20],
        "metrics": metrics[:50],
        "tags": tags[:20],
        "structured_data": {
            "source_file": source_name,
            "maturity_basis": "source_explicit" if maturity_level is not None else "not_stated",
            "evidence_score_basis": "field_completeness_heuristic",
            "entity_counts": {key: len(value) for key, value in sorted(grouped.items())},
            "original": document,
        },
        "evidence_score": evidence_score,
        "status": "approved",
        "taxonomy_code": taxonomy_code,
        "source": {
            "source_type": "local_seed",
            "title": source_name,
            "publisher": "水利科技成果资料",
            "authority_level": "official",
            "is_primary_source": True,
            "credibility_score": 0.9,
        },
    }


def normalize_directory(source_dir: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(source_dir.glob("*.json")):
        with path.open("r", encoding="utf-8-sig") as handle:
            document = json.load(handle)
        records.append(normalize_document(document, path.name))
    if not records:
        raise ValueError(f"目录中没有 JSON 文件：{source_dir}")
    return records


def write_seed_file(records: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def import_seed_records(
    session: AsyncSession,
    records: list[dict[str, Any]],
    organization_id: str,
) -> dict[str, int]:
    taxonomy_rows = (await session.execute(select(Taxonomy.id, Taxonomy.code))).all()
    taxonomy_ids = {row.code: row.id for row in taxonomy_rows}
    created = 0
    skipped = 0
    for raw in records:
        record = dict(raw)
        source = dict(record.pop("source", {}))
        taxonomy_code = str(record.pop("taxonomy_code", "water-resource"))
        normalized = str(record["normalized_title"])
        existing = await session.scalar(
            select(Achievement).where(
                Achievement.organization_id == organization_id,
                Achievement.normalized_title == normalized,
            )
        )
        if existing is not None:
            skipped += 1
            continue
        achievement = Achievement(
            organization_id=organization_id,
            taxonomy_id=taxonomy_ids.get(taxonomy_code),
            **record,
        )
        session.add(achievement)
        await session.flush()
        source_name = str(source.get("title", "本地成果资料"))
        session.add(
            AchievementSource(
                achievement_id=achievement.id,
                source_type=str(source.get("source_type", "local_seed")),
                title=source_name,
                canonical_url_hash=hashlib.sha256(source_name.encode()).hexdigest(),
                publisher=str(source.get("publisher", "")),
                authority_level=str(source.get("authority_level", "unknown")),
                is_primary_source=bool(source.get("is_primary_source", True)),
                credibility_score=float(source.get("credibility_score", 0.8)),
                evidence=[
                    {
                        "field": "structured_data",
                        "source_file": source_name,
                        "support_type": "direct",
                    }
                ],
            )
        )
        session.add(
            OutboxEvent(
                aggregate_type="achievement",
                aggregate_id=achievement.id,
                event_type="achievement.approved",
                version=achievement.version,
                payload={
                    "achievement_id": achievement.id,
                    "version": achievement.version,
                    "source": "seed_data",
                },
            )
        )
        created += 1
    await session.commit()
    return {"created": created, "skipped": skipped}


async def load_seed_file(path: Path, organization_id: str) -> dict[str, int]:
    seed_text = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
    records = json.loads(seed_text)
    if not isinstance(records, list):
        raise ValueError("种子数据顶层必须是数组")
    async with AsyncSessionFactory() as session:
        return await import_seed_records(session, records, organization_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="标准化并导入水利科技成果 JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    normalize_parser = subparsers.add_parser("normalize")
    normalize_parser.add_argument("--source", type=Path, required=True)
    normalize_parser.add_argument("--output", type=Path, required=True)
    load_parser = subparsers.add_parser("load")
    load_parser.add_argument("--seed", type=Path, required=True)
    load_parser.add_argument("--organization-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "normalize":
        records = normalize_directory(args.source)
        write_seed_file(records, args.output)
        print(f"已标准化 {len(records)} 条成果：{args.output}")
    else:
        result = asyncio.run(load_seed_file(args.seed, args.organization_id))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
