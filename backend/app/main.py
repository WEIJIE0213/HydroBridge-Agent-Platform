from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.container import ServiceContainer

REQUESTS = Counter(
    "hydrobridge_http_requests_total",
    "HTTP request count",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "hydrobridge_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    app.state.services = ServiceContainer.create()
    if settings.app_env != "test":
        await app.state.services.object_storage.ensure_buckets()
    logger = structlog.get_logger("startup")
    logger.info(
        "application_started",
        environment=settings.app_env,
        model_configured=settings.model_is_configured,
        tavily_configured=settings.tavily_is_configured,
    )
    yield
    await app.state.services.close()
    logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="水利科技情报发现与供需决策多 Agent 平台",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        structlog.get_logger("http").exception(
            "unhandled_request_error",
            method=request.method,
            path=request.url.path,
        )
        raise
    elapsed = time.perf_counter() - started
    route = request.scope.get("route")
    path_template = getattr(route, "path", request.url.path)
    REQUESTS.labels(request.method, path_template, response.status_code).inc()
    LATENCY.labels(request.method, path_template).observe(elapsed)
    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
    return response


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(api_router, prefix=settings.api_prefix)
