"""ComfyUI Horror-Painting image server for Vast.ai Serverless deployment.

Stack:
  - Checkpoint: AlbedoBase XL v3.1
  - LoRAs: Detail Tweaker XL (1.0), xl_more_art-full (0.7),
           Midjourney Mimic (0.6), Impressionism (0.4), Andreas Achenbach (0.4)
  - Sampler: dpmpp_2m / karras, 34 steps, CFG 2.0
  - Resolution: 1216x832

~10-12 GB VRAM peak. Needs RTX A5000/A6000 (24 GB) or similar.

This variant runs ComfyUI headless with a direct FastAPI server (no PyWorker).
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
_COMFYUI_DIR = os.getenv("COMFYUI_DIR", "/app/ComfyUI")
_WORKFLOW_PATH = os.getenv("WORKFLOW_PATH", "/app/workflow_api.json")
_POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1.0"))
_TIMEOUT = float(os.getenv("GENERATE_TIMEOUT", "300"))  # 5 min max

_DEFAULT_NEGATIVE_PROMPT = (
    "deformed, ng_deepnegative_v1_75t, (deformed, distorted, disfigured:1.5), "
    "(mutated hands and fingers:1.5), monochrome background, furry, loli, poorly drawn, "
    "bad anatomy, wrong anatomy, extra limbs, missing limb, floating limbs, missing fingers, "
    "elongated hands, disconnected limbs, mutation, mutated, ugly, disgusting, blurry, "
    "blurry eyes, background characters, muscular, smooth, clean, minimalist, sleek, modern, "
    "photorealistic, sharp details, hyperdetailed, fine details, smooth rendering, digital art"
)

_workflow_template: dict[str, object] | None = None
_comfyui_process: subprocess.Popen[bytes] | None = None
_ready = False

# ---------------------------------------------------------------------------
# ComfyUI lifecycle
# ---------------------------------------------------------------------------


def _load_workflow() -> dict[str, object]:
    global _workflow_template
    with open(_WORKFLOW_PATH) as f:
        _workflow_template = json.load(f)
    logger.info("Workflow loaded from %s", _WORKFLOW_PATH)
    return _workflow_template


def _start_comfyui() -> subprocess.Popen[bytes]:
    """Start ComfyUI server as a subprocess."""
    global _comfyui_process
    cmd = [
        sys.executable, f"{_COMFYUI_DIR}/main.py",
        "--listen", "127.0.0.1",
        "--port", "8188",
        "--preview-method", "none",
        "--disable-auto-launch",
    ]
    logger.info("Starting ComfyUI: %s", " ".join(cmd))
    _comfyui_process = subprocess.Popen(
        cmd,
        cwd=_COMFYUI_DIR,
    )
    return _comfyui_process


def _wait_for_comfyui(timeout: float = 600) -> None:
    """Poll ComfyUI until it responds or timeout."""
    global _ready
    t0 = time.monotonic()
    with httpx.Client(timeout=5) as client:
        while time.monotonic() - t0 < timeout:
            # Fail fast if ComfyUI process died
            if _comfyui_process and _comfyui_process.poll() is not None:
                raise RuntimeError(
                    f"ComfyUI process exited with code {_comfyui_process.returncode}"
                )
            try:
                resp = client.get(f"{_COMFYUI_URL}/system_stats")
                if resp.status_code == 200:
                    _ready = True
                    logger.info(
                        "ComfyUI ready after %.1fs. System stats: %s",
                        time.monotonic() - t0,
                        resp.text[:200],
                    )
                    logger.info("Application startup complete.")
                    return
            except httpx.ConnectError:
                pass
            time.sleep(2)
    raise RuntimeError(f"ComfyUI did not start within {timeout}s")


# ---------------------------------------------------------------------------
# Workflow injection
# ---------------------------------------------------------------------------


def _build_workflow(
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int = 1216,
    height: int = 832,
    steps: int = 34,
    cfg: float = 2.0,
) -> dict[str, object]:
    """Inject request parameters into the workflow template."""
    if _workflow_template is None:
        raise RuntimeError("Workflow not loaded")

    wf: dict[str, object] = json.loads(json.dumps(_workflow_template))

    # Node 7: positive prompt
    wf["7"]["inputs"]["text"] = prompt  # type: ignore[index]
    # Node 8: negative prompt
    wf["8"]["inputs"]["text"] = negative_prompt  # type: ignore[index]
    # Node 9: resolution
    wf["9"]["inputs"]["width"] = width  # type: ignore[index]
    wf["9"]["inputs"]["height"] = height  # type: ignore[index]
    # Node 10: sampler params
    wf["10"]["inputs"]["seed"] = seed  # type: ignore[index]
    wf["10"]["inputs"]["steps"] = steps  # type: ignore[index]
    wf["10"]["inputs"]["cfg"] = cfg  # type: ignore[index]

    return wf


# ---------------------------------------------------------------------------
# ComfyUI API interaction
# ---------------------------------------------------------------------------


def _submit_prompt(workflow: dict[str, object]) -> str:
    """Submit workflow to ComfyUI and return the prompt ID."""
    client_id = str(uuid.uuid4())
    payload = {"prompt": workflow, "client_id": client_id}
    resp = httpx.post(f"{_COMFYUI_URL}/prompt", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    prompt_id: str = data["prompt_id"]
    logger.info("Submitted prompt_id=%s", prompt_id)
    return prompt_id


def _poll_result(prompt_id: str, timeout: float = _TIMEOUT) -> list[dict[str, str]]:
    """Poll /history until the prompt completes. Returns output image info."""
    t0 = time.monotonic()
    with httpx.Client(timeout=10) as client:
        while time.monotonic() - t0 < timeout:
            resp = client.get(f"{_COMFYUI_URL}/history/{prompt_id}")
            if resp.status_code == 200:
                data = resp.json()
                if prompt_id in data:
                    history = data[prompt_id]
                    if history.get("status", {}).get("completed", False) or "outputs" in history:
                        outputs = history.get("outputs", {})
                        # Find SaveImage node output (node 12)
                        for node_id, node_out in outputs.items():
                            if "images" in node_out:
                                return node_out["images"]  # type: ignore[no-any-return]
                        raise RuntimeError(f"No image output in prompt {prompt_id}")
            time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"ComfyUI did not complete prompt {prompt_id} within {timeout}s")


def _fetch_image(filename: str, subfolder: str, img_type: str = "output") -> bytes:
    """Download a generated image from ComfyUI."""
    resp = httpx.get(
        f"{_COMFYUI_URL}/view",
        params={"filename": filename, "subfolder": subfolder, "type": img_type},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _load_workflow()
    _start_comfyui()
    try:
        _wait_for_comfyui()
    except Exception:
        logger.exception("ComfyUI startup failed — server will start but /ready returns 503")
    yield
    if _comfyui_process and _comfyui_process.poll() is None:
        _comfyui_process.terminate()
        _comfyui_process.wait(timeout=10)


app = FastAPI(
    title="ComfyUI Horror-Painting Server (Vast.ai Serverless)",
    description="AlbedoBase XL + 5 LoRAs, 34-step dpmpp_2m/karras. POST /generate -> PNG.",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image prompt.")
    negative_prompt: str = Field(
        default=_DEFAULT_NEGATIVE_PROMPT,
        description="Negative prompt.",
    )
    width: int = Field(default=1216, ge=512, le=2048)
    height: int = Field(default=832, ge=512, le=2048)
    steps: int = Field(default=34, ge=1, le=100)
    cfg: float = Field(default=2.0, ge=0.0, le=20.0)
    seed: int | None = Field(None, ge=0)


class HealthResponse(BaseModel):
    status: str
    comfyui_ready: bool


class ReadyResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok", comfyui_ready=_ready)


@app.get("/ready")
def ready_check() -> ReadyResponse:
    if _ready:
        return ReadyResponse(status="ready")
    raise HTTPException(status_code=503, detail="ComfyUI loading")


@app.post(
    "/generate",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Generated PNG image"}},
)
def generate(request: GenerateRequest) -> Response:
    """Generate a horror-painting style image and return it as PNG bytes."""
    if not _ready:
        raise HTTPException(status_code=503, detail="ComfyUI not ready")

    seed = request.seed if request.seed is not None else random.randint(0, 2**53)

    logger.info(
        "Generating: steps=%d size=%dx%d cfg=%.1f seed=%d",
        request.steps, request.width, request.height, request.cfg, seed,
    )

    t0 = time.perf_counter()

    workflow = _build_workflow(
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        seed=seed,
        width=request.width,
        height=request.height,
        steps=request.steps,
        cfg=request.cfg,
    )

    try:
        prompt_id = _submit_prompt(workflow)
        images = _poll_result(prompt_id)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Generation timed out")
    except Exception as exc:
        logger.exception("Generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not images:
        raise HTTPException(status_code=500, detail="No images generated")

    img_info = images[0]
    png_bytes = _fetch_image(
        filename=img_info["filename"],
        subfolder=img_info.get("subfolder", ""),
        img_type=img_info.get("type", "output"),
    )

    elapsed = time.perf_counter() - t0
    logger.info("Done in %.1fs", elapsed)

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"X-Seed": str(seed), "X-Elapsed": f"{elapsed:.2f}"},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8006, reload=False)
