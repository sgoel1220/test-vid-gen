"""FastAPI application factory — wires up middleware, mounts, lifespan, and routes."""

from __future__ import annotations

import asyncio
import logging
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import engine
import persistence as _persist
from config import config_manager, get_host, get_output_path, get_ssl_config
from routes import router
from image_routes import image_router
from routes_history import history_router

logger = logging.getLogger(__name__)

_LITE_UI_PATH = Path(__file__).parent / "lite_ui"


def _suppress_known_startup_warnings() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"`LoRACompatibleLinear` is deprecated and will be removed in version 1\.0\.0\..*",
        category=FutureWarning,
        module=r"diffusers\.models\.lora",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"You are using `torch\.load` with `weights_only=False`.*",
        category=FutureWarning,
        module=r"perth\.perth_net\.perth_net_implicit\.checkpoint_manager",
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Starting background TTS model load...")
    if not engine.start_background_model_load():
        logger.info("Startup found an existing model load state.")

    # --- Persistence outbox lifecycle ---
    _drain_task: asyncio.Task | None = None
    _outbox: _persist.Outbox | None = None
    _client: _persist.PersistenceClient | None = None
    if _persist.is_enabled():
        _client = _persist.get_client()
        _outbox = _persist.Outbox()
        await _outbox.open()
        _persist.set_outbox(_outbox)
        # Start background drain loop immediately, but don't block startup
        _drain_task = asyncio.create_task(_outbox.background_drain_loop(_client))
        logger.info("Persistence outbox started (background drain active).")
    else:
        logger.info("Persistence disabled (METADATA_API_URL not set).")

    yield

    # --- Shutdown: cancel drain loop, close outbox and client ---
    if _drain_task is not None:
        _drain_task.cancel()
        try:
            await _drain_task
        except asyncio.CancelledError:
            pass
    if _outbox is not None:
        await _outbox.aclose()
    if _client is not None:
        await _client.aclose()
    _persist.set_outbox(None)


def create_app() -> FastAPI:
    _suppress_known_startup_warnings()

    app = FastAPI(
        title="Chatterbox Lite Clone Server",
        description="Minimal clone-only Chatterbox API that saves every generated chunk.",
        version="0.1.0",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*", "null"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    outputs_path = get_output_path(ensure_absolute=True)
    outputs_path.mkdir(parents=True, exist_ok=True)
    app.mount("/outputs", StaticFiles(directory=str(outputs_path)), name="outputs")

    if not _LITE_UI_PATH.is_dir():
        logger.warning(
            "Lite UI static assets directory not found at '%s'. The lite UI will be unavailable.",
            _LITE_UI_PATH,
        )

    app.include_router(router)
    app.include_router(image_router)
    app.include_router(history_router)

    @app.get("/", include_in_schema=False)
    async def root():
        index_file = _LITE_UI_PATH / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Lite UI index not found.")

    @app.get("/styles.css", include_in_schema=False)
    async def lite_styles():
        styles_file = _LITE_UI_PATH / "styles.css"
        if styles_file.is_file():
            return FileResponse(styles_file)
        raise HTTPException(status_code=404, detail="styles.css not found")

    @app.get("/script.js", include_in_schema=False)
    async def lite_script():
        script_file = _LITE_UI_PATH / "script.js"
        if script_file.is_file():
            return FileResponse(script_file)
        raise HTTPException(status_code=404, detail="script.js not found")

    return app


app = create_app()

if __name__ == "__main__":
    host = get_host()
    port = int(config_manager.get_int("lite_server.port", 8005))
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=False,
        **get_ssl_config(),
    )
