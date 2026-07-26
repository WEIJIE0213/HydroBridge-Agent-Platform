from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.db.models.domain import Achievement
from app.db.models.knowledge import OutboxEvent
from app.db.session import AsyncSessionFactory, engine
from app.services.container import ServiceContainer
from app.worker.celery_app import celery_app

logger = get_task_logger(__name__)


async def _run_isolated(awaitable):
    """Release async DB connections before Celery closes the task event loop."""
    try:
        return await awaitable
    finally:
        await engine.dispose()


async def _claim_outbox_batch(limit: int = 20) -> list[str]:
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending")
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars())
        for event in events:
            event.status = "processing"
            event.attempts += 1
        await session.commit()
        return [event.id for event in events]


async def _mark_outbox_result(
    event_id: str,
    success: bool,
    error: str = "",
) -> None:
    async with AsyncSessionFactory() as session:
        event = await session.get(OutboxEvent, event_id)
        if event is None:
            return
        event.status = (
            "completed"
            if success
            else "failed"
            if event.attempts >= 8
            else "pending"
        )
        event.last_error = error[:20_000]
        if success:
            event.payload = {
                **event.payload,
                "processed_at": datetime.now(UTC).isoformat(),
            }
        await session.commit()


async def _process_outbox() -> dict[str, int]:
    event_ids = await _claim_outbox_batch()
    completed = 0
    failed = 0
    services = ServiceContainer.create()
    try:
        for event_id in event_ids:
            try:
                async with AsyncSessionFactory() as session:
                    event = await session.get(OutboxEvent, event_id)
                    if event is None:
                        continue
                    if event.aggregate_type != "achievement":
                        raise ValueError(
                            f"Unsupported outbox aggregate: {event.aggregate_type}"
                        )
                    achievement = await session.get(Achievement, event.aggregate_id)
                    if achievement is None:
                        await services.retrieval.delete_achievement(event.aggregate_id)
                    elif achievement.status == "approved":
                        await services.retrieval.index_achievement(achievement)
                        achievement.qdrant_indexed = True
                        await session.commit()
                    else:
                        await services.retrieval.delete_achievement(achievement.id)
                        achievement.qdrant_indexed = False
                        await session.commit()
                await _mark_outbox_result(event_id, True)
                completed += 1
            except Exception as exc:
                await _mark_outbox_result(event_id, False, str(exc))
                failed += 1
                logger.exception("outbox_event_failed", extra={"event_id": event_id})
    finally:
        await services.close()
    return {"claimed": len(event_ids), "completed": completed, "failed": failed}


@celery_app.task(name="app.worker.tasks.process_outbox")
def process_outbox() -> dict[str, int]:
    return asyncio.run(_run_isolated(_process_outbox()))


async def _execute_agent(run_id: str, resume_payload: dict | None = None) -> None:
    services = ServiceContainer.create()
    try:
        if services.agent_runtime is None:
            raise RuntimeError("Agent Runtime was not initialized")
        await services.agent_runtime.execute(run_id, resume_payload)
    finally:
        await services.close()


@celery_app.task(name="app.worker.tasks.execute_agent_run")
def execute_agent_run(run_id: str) -> None:
    asyncio.run(_run_isolated(_execute_agent(run_id)))


@celery_app.task(name="app.worker.tasks.resume_agent_run")
def resume_agent_run(run_id: str, resume_payload: dict) -> None:
    asyncio.run(_run_isolated(_execute_agent(run_id, resume_payload)))


@celery_app.task(name="app.worker.tasks.run_monitoring_scan")
def run_monitoring_scan() -> dict[str, str]:
    # The real research workflow is invoked through AgentRuntime once live
    # credentials are configured. Keeping the scheduler task explicit allows it
    # to be observed, retried and disabled independently.
    return {"status": "scheduled", "workflow": "technology_monitoring"}


@celery_app.task(name="app.worker.tasks.run_evaluation")
def run_evaluation(eval_run_id: str) -> dict[str, str]:
    return {"status": "queued", "eval_run_id": eval_run_id}
