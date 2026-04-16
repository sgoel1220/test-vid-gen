"""FastAPI application factory for Creepy Brain service"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
import structlog

import app.metrics as _metrics  # noqa: F401 — registers all metric objects

from app.config import settings
from app.db import close_db, init_db
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routes import blobs, health, runs, voices
from app.schemas import HealthResponse, ServiceInfo

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager"""
    logger.info("startup", service="creepy-brain", port=settings.port)

    # Initialize database connection pool
    await init_db()
    logger.info("database_initialized", database_url=settings.database_url.split('@')[1])

    # TODO: Initialize Hatchet client

    yield

    # Cleanup on shutdown
    await close_db()
    logger.info("shutdown", service="creepy-brain")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application"""

    # Configure structured logging
    configure_logging(json_logs=settings.json_logs)

    app = FastAPI(
        title="Creepy Brain",
        description="Content Pipeline Workflow Orchestration Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add request context middleware
    app.add_middleware(RequestContextMiddleware)

    # Auto-instrument HTTP metrics and expose /metrics endpoint
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    # Register API routers
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(voices.router)
    app.include_router(blobs.router)

    from app.routes.stories import router as stories_router
    app.include_router(stories_router)

    @app.get("/health")
    async def health_check() -> HealthResponse:
        """Health check endpoint"""
        return HealthResponse(status="ok")

    @app.get("/")
    async def root() -> ServiceInfo:
        """Root endpoint"""
        return ServiceInfo(
            service="creepy-brain",
            version="0.1.0",
            status="running",
        )

    return app


# Export app instance for uvicorn
app = create_app()
