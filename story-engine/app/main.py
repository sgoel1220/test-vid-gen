"""FastAPI application factory for story-engine."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import generate, status

UI_DIR = Path(__file__).parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Creepy Pasta Story Engine",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(generate.router)
    app.include_router(status.router)

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app


app = create_app()
