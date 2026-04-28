"""FastAPI application factory for Creepy Brain service"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
import structlog

import app.metrics as _metrics  # noqa: F401 — registers all metric objects

from app.config import settings
from app.db import close_db, init_db
from app.engine import CronScheduler, engine
from app.llm.client import close_llm_provider
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routes import blobs, health, runs, voices, workflows
from app.services.errors import ResourceNotFoundError

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager"""
    logger.info("startup", service="creepy-brain", port=settings.port)

    # Initialize database connection pool
    await init_db()
    logger.info("database_initialized", database_url=settings.database_url.split("@")[1])

    # Register all workflow definitions with the engine singleton.
    from app.workflows import register_workflows
    register_workflows()

    # Start cron scheduler for periodic workflows (recon every 5 min).
    from app.workflows.recon import RECON_CRON
    from app.workflows.schemas import EmptyWorkflowInput

    scheduler = CronScheduler(engine)
    scheduler.add(RECON_CRON, "ReconOrphanedPods", EmptyWorkflowInput)
    await scheduler.start()

    # Start workflow engine.
    logger.info("workflow_engine_started")

    yield

    # Shutdown: stop engine (cancels running workflows) and scheduler.
    await engine.stop()
    await scheduler.stop()
    logger.info("workflow_engine_stopped")

    # Cleanup outbound LLM clients.
    await close_llm_provider()

    # Cleanup DB connection pool.
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

    # Centralised exception → HTTP status mapping.
    # Routes must NOT catch these themselves — just let them propagate.
    @app.exception_handler(ResourceNotFoundError)
    async def _not_found_handler(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # Auto-instrument HTTP metrics and expose /metrics endpoint
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    # Register API routers
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(voices.router)
    app.include_router(blobs.router)
    app.include_router(workflows.router)

    from app.routes.stories import router as stories_router

    app.include_router(stories_router)

    from app.routes.costs import router as costs_router

    app.include_router(costs_router)

    from app.routes.image import router as image_router

    app.include_router(image_router)

    # Mount static files for serving audio and UI
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


# Export app instance for uvicorn
app = create_app()
