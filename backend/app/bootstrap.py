from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.models.domain import Achievement, EvaluationRubric, Taxonomy
from app.db.models.identity import Organization, Role, User, UserRole
from app.db.models.knowledge import OutboxEvent
from app.db.session import AsyncSessionFactory
from app.importers.water_data import import_seed_records

logger = get_logger("bootstrap")

SYSTEM_ROLES = {
    "admin": "系统管理和全部审核权限",
    "operator": "成果运营、资料入库和任务处理",
    "expert": "成果评估、匹配复核和审核",
    "researcher": "成果检索、研究与知识库维护",
    "user": "提交需求、运行 Agent 和查看本人结果",
}

TAXONOMIES = [
    ("flood", "防洪减灾"),
    ("irrigation", "灌区与农村水利"),
    ("water-resource", "水资源配置与节水"),
    ("ecology", "水生态保护与修复"),
    ("hydraulic-structure", "水工结构与工程安全"),
    ("smart-water", "智慧水利与数字孪生"),
    ("water-supply", "城乡供水与水处理"),
    ("soil-water", "水土保持"),
]


async def bootstrap() -> None:
    configure_logging(settings.log_level)
    async with AsyncSessionFactory() as session:
        organization = (
            await session.execute(select(Organization).where(Organization.slug == "hydrobridge"))
        ).scalar_one_or_none()
        if organization is None:
            organization = Organization(
                name="HydroBridge AI 演示组织",
                slug="hydrobridge",
                organization_type="platform",
            )
            session.add(organization)
            await session.flush()

        roles: dict[str, Role] = {}
        for role_name, description in SYSTEM_ROLES.items():
            role = (
                await session.execute(select(Role).where(Role.name == role_name))
            ).scalar_one_or_none()
            if role is None:
                role = Role(name=role_name, description=description, is_system=True)
                session.add(role)
                await session.flush()
            roles[role_name] = role

        admin = (
            await session.execute(
                select(User).where(User.email == settings.bootstrap_admin_email.lower())
            )
        ).scalar_one_or_none()
        if admin is None:
            admin = User(
                organization_id=organization.id,
                email=settings.bootstrap_admin_email.lower(),
                display_name="平台管理员",
                password_hash=hash_password(settings.bootstrap_admin_password),
                is_superuser=True,
            )
            session.add(admin)
            await session.flush()
            session.add(UserRole(user_id=admin.id, role_id=roles["admin"].id))

        for code, name in TAXONOMIES:
            exists = (
                await session.execute(select(Taxonomy).where(Taxonomy.code == code))
            ).scalar_one_or_none()
            if exists is None:
                session.add(Taxonomy(code=code, name=name))

        rubric = (
            await session.execute(
                select(EvaluationRubric).where(
                    EvaluationRubric.name == "水利科技成果综合评价",
                    EvaluationRubric.version == 1,
                )
            )
        ).scalar_one_or_none()
        if rubric is None:
            session.add(
                EvaluationRubric(
                    name="水利科技成果综合评价",
                    version=1,
                    dimensions=[
                        {"key": "scenario_fit", "name": "场景适配度", "weight": 0.30},
                        {"key": "effectiveness", "name": "技术效果", "weight": 0.20},
                        {"key": "maturity", "name": "工程成熟度", "weight": 0.15},
                        {"key": "evidence", "name": "证据可信度", "weight": 0.15},
                        {"key": "cost", "name": "成本适配度", "weight": 0.10},
                        {"key": "risk", "name": "实施风险", "weight": 0.10},
                    ],
                )
            )
        intrinsic_rubric = (
            await session.execute(
                select(EvaluationRubric).where(
                    EvaluationRubric.name == "水利科技成果自身质量评价",
                    EvaluationRubric.version == 1,
                )
            )
        ).scalar_one_or_none()
        if intrinsic_rubric is None:
            session.add(
                EvaluationRubric(
                    name="水利科技成果自身质量评价",
                    version=1,
                    dimensions=[
                        {"key": "innovation", "name": "创新性", "weight": 0.20},
                        {"key": "effectiveness", "name": "技术有效性", "weight": 0.20},
                        {"key": "maturity", "name": "工程成熟度", "weight": 0.15},
                        {
                            "key": "evidence_quality",
                            "name": "证据充分性",
                            "weight": 0.20,
                        },
                        {
                            "key": "promotion_value",
                            "name": "推广复制价值",
                            "weight": 0.15,
                        },
                        {
                            "key": "implementation_feasibility",
                            "name": "经济与实施可行性",
                            "weight": 0.10,
                        },
                    ],
                )
            )
        await session.commit()
        seed_result = {"created": 0, "skipped": 0}
        seed_path = Path(settings.bootstrap_seed_path)
        seed_exists = await asyncio.to_thread(seed_path.is_file)
        if settings.bootstrap_seed_data and seed_exists:
            seed_text = await asyncio.to_thread(
                seed_path.read_text,
                encoding="utf-8-sig",
            )
            records = json.loads(seed_text)
            if not isinstance(records, list):
                raise ValueError("种子数据顶层必须是数组")
            seed_result = await import_seed_records(
                session,
                records,
                organization.id,
            )
        approved_achievements = (
            await session.execute(select(Achievement).where(Achievement.status == "approved"))
        ).scalars()
        reindex_created = 0
        for achievement in approved_achievements:
            reindex_exists = await session.scalar(
                select(OutboxEvent.id).where(
                    OutboxEvent.aggregate_type == "achievement",
                    OutboxEvent.aggregate_id == achievement.id,
                    OutboxEvent.event_type == "achievement.index_schema",
                    OutboxEvent.version == settings.knowledge_index_version,
                )
            )
            if reindex_exists is None:
                achievement.search_indexed = False
                session.add(
                    OutboxEvent(
                        aggregate_type="achievement",
                        aggregate_id=achievement.id,
                        event_type="achievement.index_schema",
                        version=settings.knowledge_index_version,
                        payload={
                            "achievement_id": achievement.id,
                            "achievement_version": achievement.version,
                            "index_version": settings.knowledge_index_version,
                        },
                    )
                )
                reindex_created += 1
        await session.commit()
        logger.info("bootstrap_complete", admin_email=settings.bootstrap_admin_email)
        logger.info("seed_data_complete", **seed_result)
        logger.info(
            "knowledge_index_reconciliation_complete",
            index_version=settings.knowledge_index_version,
            created=reindex_created,
        )


if __name__ == "__main__":
    asyncio.run(bootstrap())
