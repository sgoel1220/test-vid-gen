"""FastAPI application factory for Creepy Brain service"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
import structlog

import app.metrics as _metrics  # noqa: F401 — registers all metric objects

from app.config import settings
from app.db import close_db, init_db
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routes import blobs, health, runs, voices, workflows

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager"""
    logger.info("startup", service="creepy-brain", port=settings.port)

    # Initialize database connection pool
    await init_db()
    logger.info("database_initialized", database_url=settings.database_url.split('@')[1])

    # Interim task registry — replaced by Hatchet in bead Chatterbox-TTS-Server-104
    app.state.background_tasks: set[asyncio.Task[None]] = set()  # type: ignore[misc]

    # Semaphore to cap concurrent story generation runs
    app.state.generation_semaphore = asyncio.Semaphore(settings.max_concurrent_generations)

    # Hatchet worker runs as a separate process (see app/workflows/worker.py)

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
    app.include_router(workflows.router)

    from app.routes.stories import router as stories_router

    app.include_router(stories_router)

    from app.routes.costs import router as costs_router
    app.include_router(costs_router)

    # Mount static files for serving audio and UI
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


# Export app instance for uvicorn
app = create_app()
