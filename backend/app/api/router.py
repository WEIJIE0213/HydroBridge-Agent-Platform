from fastapi import APIRouter

from app.api.routes import (
    achievements,
    artifacts,
    auth,
    health,
    overview,
    requirements,
    runs,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(artifacts.router)
api_router.include_router(achievements.router)
api_router.include_router(requirements.router)
api_router.include_router(runs.router)
api_router.include_router(overview.router)
