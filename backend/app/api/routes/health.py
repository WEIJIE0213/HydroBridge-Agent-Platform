from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DatabaseSession, Services
from app.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@router.get("/readiness")
async def readiness(
    session: DatabaseSession,
    services: Services,
) -> dict[str, object]:
    checks: dict[str, object] = {
        "database": False,
        "redis": await services.redis.health(),
        "qdrant": await services.vector_store.health(),
        "minio": await services.object_storage.health(),
        "tavily_configured": services.search.configured,
        "model_configured": settings.model_is_configured,
    }
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False

    critical_ready = bool(
        checks["database"]
        and isinstance(checks["redis"], dict)
        and checks["redis"].get("runtime")
        and checks["redis"].get("cache")
        and checks["qdrant"]
        and checks["minio"]
    )
    return {"status": "ready" if critical_ready else "degraded", "checks": checks}
