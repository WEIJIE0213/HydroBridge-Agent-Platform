from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException

from app.api.deps import Services
from app.core.config import settings


def dispatch_agent_run(
    background_tasks: BackgroundTasks,
    services: Services,
    run_id: str,
) -> None:
    if services.agent_runtime is None:
        raise HTTPException(status_code=503, detail="Agent Runtime 未初始化")
    if settings.agent_execution_mode == "inline":
        background_tasks.add_task(services.agent_runtime.execute, run_id)
    else:
        from app.worker.tasks import execute_agent_run

        execute_agent_run.delay(run_id)
