"""FastAPI application factory for Creepy Brain service"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import structlog

from app.config import settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager"""
    logger.info("startup", service="creepy-brain", port=settings.port)

    # TODO: Initialize database connection pool
    # TODO: Initialize Hatchet client

    yield

    # Cleanup on shutdown
    logger.info("shutdown", service="creepy-brain")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application"""

    app = FastAPI(
        title="Creepy Brain",
        description="Content Pipeline Workflow Orchestration Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return JSONResponse(
            content={"status": "ok"},
            status_code=200,
        )

    @app.get("/")
    async def root():
        """Root endpoint"""
        return {
            "service": "creepy-brain",
            "version": "0.1.0",
            "status": "running",
        }

    return app


# Export app instance for uvicorn
app = create_app()
