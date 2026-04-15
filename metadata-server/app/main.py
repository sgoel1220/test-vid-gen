"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.storage import LocalFilesystemAudioStore, set_audio_store
from app.routes import health
from app.routes import runs, runs_by_pod, chunks, scripts, stories, voices, audio as audio_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    set_audio_store(LocalFilesystemAudioStore(settings.audio_storage_root))
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Creepy Pasta Metadata Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(scripts.router)
    app.include_router(voices.router)
    app.include_router(runs.router)
    app.include_router(runs_by_pod.router)
    app.include_router(stories.router)
    app.include_router(chunks.router)
    app.include_router(audio_routes.router)
    return app


app = create_app()
