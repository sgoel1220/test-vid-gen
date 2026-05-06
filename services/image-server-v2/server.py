"""SDXL Impressionist image server — POST a prompt, get a PNG back.

Stack:
  - Base: SDXL 1.0 (stabilityai/stable-diffusion-xl-base-1.0)
  - Style: Impressionism SDXL LoRA (CivitAI 133465, strength 0.8)
  - Speed: SDXL-Lightning 4-step LoRA (ByteDance, strength 1.0)
  - VAE: madebyollin/sdxl-vae-fp16-fix
  - Scheduler: Euler, sgm_uniform spacing
  - 4 steps, cfg 2.0

~7-8 GB VRAM peak. Fits RTX A4000 (16 GB) comfortably.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import json
import logging
import os
import threading
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import torch
from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline, AutoencoderKL
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from huggingface_hub import hf_hub_download
from PIL import Image
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_BASE_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
_VAE_MODEL = "madebyollin/sdxl-vae-fp16-fix"
_LIGHTNING_REPO = "ByteDance/SDXL-Lightning"
_LIGHTNING_LORA = "sdxl_lightning_4step_lora.safetensors"

# Impressionism LoRA — downloaded at build time to /app/loras/
_IMPRESSIONISM_LORA_PATH = os.getenv(
    "IMPRESSIONISM_LORA_PATH", "/app/loras/impressionism_sdxl.safetensors"
)
_IMPRESSIONISM_STRENGTH = float(os.getenv("IMPRESSIONISM_STRENGTH", "0.8"))
_CIVITAI_TOKEN = os.getenv("CIVITAI_TOKEN", "")

# Tunable generation defaults (override via env to avoid rebuilds)
_DEFAULT_STEPS = int(os.getenv("DEFAULT_STEPS", "4"))
_DEFAULT_GUIDANCE_SCALE = float(os.getenv("DEFAULT_GUIDANCE_SCALE", "2.0"))
_DEFAULT_WIDTH = int(os.getenv("DEFAULT_WIDTH", "1280"))
_DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", "720"))
_DEFAULT_NEGATIVE_PROMPT = os.getenv(
    "DEFAULT_NEGATIVE_PROMPT",
    "photorealistic, photograph, blurry, low quality, watermark, text, deformed",
)

_pipe: StableDiffusionXLPipeline | None = None

# Runtime loading state — populated by _load()
_load_state: dict[str, object] = {
    "impressionism_loaded": False,
    "lightning_loaded": False,
    "fused": False,
    "load_error": None,
    "load_time_s": None,
}

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load() -> StableDiffusionXLPipeline:
    global _pipe
    if _pipe is not None:
        return _pipe

    _load_state.update(impressionism_loaded=False, lightning_loaded=False, fused=False, load_error=None, load_time_s=None)
    t_start = time.perf_counter()

    def _elapsed() -> str:
        return f"{time.perf_counter() - t_start:.1f}s"

    try:
        logger.info("[1/7] Loading VAE (madebyollin/sdxl-vae-fp16-fix)...")
        vae = AutoencoderKL.from_pretrained(
            _VAE_MODEL,
            torch_dtype=torch.float16,
        )
        logger.info("[1/7] VAE loaded. (%s)", _elapsed())

        logger.info("[2/7] Loading SDXL 1.0 base pipeline...")
        _pipe = StableDiffusionXLPipeline.from_pretrained(
            _BASE_MODEL,
            vae=vae,
            torch_dtype=torch.float16,
            variant="fp16",
        ).to("cuda")
        logger.info("[2/7] Base pipeline on CUDA. VRAM: %.2f GB (%s)",
                    torch.cuda.memory_allocated() / 1024**3, _elapsed())

        # Download Impressionism LoRA from CivitAI if not already cached
        _has_impressionism = os.path.exists(_IMPRESSIONISM_LORA_PATH)
        if not _has_impressionism:
            if _CIVITAI_TOKEN:
                logger.info("[3/7] Downloading Impressionism LoRA from CivitAI...")
                os.makedirs(os.path.dirname(_IMPRESSIONISM_LORA_PATH), exist_ok=True)
                url = f"https://civitai.com/api/download/models/133465?token={_CIVITAI_TOKEN}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(_IMPRESSIONISM_LORA_PATH, "wb") as f:
                    f.write(resp.read())
                _has_impressionism = True
                logger.info("[3/7] Impressionism LoRA downloaded. (%s)", _elapsed())
            else:
                logger.warning("[3/7] CIVITAI_TOKEN not set — skipping Impressionism LoRA. Running Lightning-only.")
        else:
            logger.info("[3/7] Impressionism LoRA already cached, skipping download.")

        if _has_impressionism:
            logger.info("[4/7] Loading Impressionism LoRA (strength=%.2f)...", _IMPRESSIONISM_STRENGTH)
            _pipe.load_lora_weights(
                _IMPRESSIONISM_LORA_PATH,
                adapter_name="impressionism",
            )
            _load_state["impressionism_loaded"] = True
            logger.info("[4/7] Impressionism LoRA loaded. (%s)", _elapsed())
        else:
            logger.info("[4/7] Skipping Impressionism LoRA.")

        logger.info("[5/7] Loading SDXL-Lightning 4-step LoRA...")
        lightning_path = hf_hub_download(_LIGHTNING_REPO, _LIGHTNING_LORA)
        _pipe.load_lora_weights(
            lightning_path,
            adapter_name="lightning",
        )
        _load_state["lightning_loaded"] = True
        logger.info("[5/7] Lightning LoRA loaded. (%s)", _elapsed())

        if _has_impressionism:
            logger.info("[6/7] Fusing LoRAs (impressionism=%.2f, lightning=1.0)...", _IMPRESSIONISM_STRENGTH)
            _pipe.set_adapters(
                ["impressionism", "lightning"],
                adapter_weights=[_IMPRESSIONISM_STRENGTH, 1.0],
            )
        else:
            logger.info("[6/7] Fusing Lightning LoRA only...")
            _pipe.set_adapters(["lightning"], adapter_weights=[1.0])
        _pipe.fuse_lora()
        _pipe.unload_lora_weights()
        _load_state["fused"] = True
        logger.info("[6/7] LoRAs fused and unloaded. VRAM: %.2f GB (%s)",
                    torch.cuda.memory_allocated() / 1024**3, _elapsed())

        logger.info("[7/7] Configuring scheduler and finalizing...")
        _pipe.scheduler = EulerDiscreteScheduler.from_config(
            _pipe.scheduler.config,
            timestep_spacing="trailing",
        )
        _pipe.set_progress_bar_config(disable=True)
        torch.cuda.empty_cache()
        gc.collect()

        _load_state["load_time_s"] = round(time.perf_counter() - t_start, 1)
        vram_gb = torch.cuda.memory_allocated() / 1024**3
        logger.info("Pipeline ready. VRAM: %.2f GB — total startup: %s", vram_gb, _elapsed())

    except Exception as exc:
        _load_state["load_error"] = str(exc)
        logger.exception("Pipeline loading failed")
        raise

    return _pipe

    t_start = time.perf_counter()

    def _elapsed() -> str:
        return f"{time.perf_counter() - t_start:.1f}s"

    logger.info("[1/7] Loading VAE (madebyollin/sdxl-vae-fp16-fix)...")
    vae = AutoencoderKL.from_pretrained(
        _VAE_MODEL,
        torch_dtype=torch.float16,
    )
    logger.info("[1/7] VAE loaded. (%s)", _elapsed())

    logger.info("[2/7] Loading SDXL 1.0 base pipeline...")
    _pipe = StableDiffusionXLPipeline.from_pretrained(
        _BASE_MODEL,
        vae=vae,
        torch_dtype=torch.float16,
        variant="fp16",
    ).to("cuda")
    logger.info("[2/7] Base pipeline on CUDA. VRAM: %.2f GB (%s)",
                torch.cuda.memory_allocated() / 1024**3, _elapsed())

    # Download Impressionism LoRA from CivitAI if not already cached
    _has_impressionism = os.path.exists(_IMPRESSIONISM_LORA_PATH)
    if not _has_impressionism:
        if _CIVITAI_TOKEN:
            logger.info("[3/7] Downloading Impressionism LoRA from CivitAI...")
            os.makedirs(os.path.dirname(_IMPRESSIONISM_LORA_PATH), exist_ok=True)
            url = f"https://civitai.com/api/download/models/133465?token={_CIVITAI_TOKEN}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as resp, open(_IMPRESSIONISM_LORA_PATH, "wb") as f:
                f.write(resp.read())
            _has_impressionism = True
            logger.info("[3/7] Impressionism LoRA downloaded. (%s)", _elapsed())
        else:
            logger.warning("[3/7] CIVITAI_TOKEN not set — skipping Impressionism LoRA. Running Lightning-only.")
    else:
        logger.info("[3/7] Impressionism LoRA already cached, skipping download.")

    if _has_impressionism:
        logger.info("[4/7] Loading Impressionism LoRA (strength=%.2f)...", _IMPRESSIONISM_STRENGTH)
        _pipe.load_lora_weights(
            _IMPRESSIONISM_LORA_PATH,
            adapter_name="impressionism",
        )
        logger.info("[4/7] Impressionism LoRA loaded. (%s)", _elapsed())
    else:
        logger.info("[4/7] Skipping Impressionism LoRA.")

    logger.info("[5/7] Loading SDXL-Lightning 4-step LoRA...")
    lightning_path = hf_hub_download(_LIGHTNING_REPO, _LIGHTNING_LORA)
    _pipe.load_lora_weights(
        lightning_path,
        adapter_name="lightning",
    )
    logger.info("[5/7] Lightning LoRA loaded. (%s)", _elapsed())

    if _has_impressionism:
        logger.info("[6/7] Fusing LoRAs (impressionism=%.2f, lightning=1.0)...", _IMPRESSIONISM_STRENGTH)
        _pipe.set_adapters(
            ["impressionism", "lightning"],
            adapter_weights=[_IMPRESSIONISM_STRENGTH, 1.0],
        )
    else:
        logger.info("[6/7] Fusing Lightning LoRA only...")
        _pipe.set_adapters(["lightning"], adapter_weights=[1.0])
    _pipe.fuse_lora()
    _pipe.unload_lora_weights()
    logger.info("[6/7] LoRAs fused and unloaded. VRAM: %.2f GB (%s)",
                torch.cuda.memory_allocated() / 1024**3, _elapsed())

    logger.info("[7/7] Configuring scheduler and finalizing...")
    _pipe.scheduler = EulerDiscreteScheduler.from_config(
        _pipe.scheduler.config,
        timestep_spacing="trailing",
    )
    _pipe.set_progress_bar_config(disable=True)
    torch.cuda.empty_cache()
    gc.collect()

    vram_gb = torch.cuda.memory_allocated() / 1024**3
    logger.info("Pipeline ready. VRAM: %.2f GB — total startup: %s", vram_gb, _elapsed())

    return _pipe


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not torch.cuda.is_available():
        logger.error("CUDA not available — cannot serve. Check GPU/driver.")
        # Don't crash; start server so /health can report the problem
        yield
        return
    try:
        _load()
    except Exception:
        logger.exception("Model loading failed — server will start but /ready returns 503")
    yield


app = FastAPI(
    title="Image Server v2",
    description=(
        "SDXL + Impressionism LoRA + Lightning 4-step. POST /generate → PNG.\n\n"
        "**Live preview:** open [/viewer](/viewer) to watch each denoising step render in real time."
    ),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image prompt.")
    negative_prompt: str = Field(
        default_factory=lambda: _DEFAULT_NEGATIVE_PROMPT,
        description="Negative prompt.",
    )
    width: int = Field(default_factory=lambda: _DEFAULT_WIDTH, ge=512, le=1536)
    height: int = Field(default_factory=lambda: _DEFAULT_HEIGHT, ge=512, le=1536)
    steps: int = Field(default_factory=lambda: _DEFAULT_STEPS, ge=1, le=8)
    guidance_scale: float = Field(default_factory=lambda: _DEFAULT_GUIDANCE_SCALE, ge=0.0, le=5.0)
    seed: int | None = Field(None, ge=0)



class ConfigResponse(BaseModel):
    impressionism_strength: float
    default_steps: int
    default_guidance_scale: float
    default_width: int
    default_height: int
    default_negative_prompt: str


class ConfigUpdate(BaseModel):
    impressionism_strength: float | None = Field(None, ge=0.0, le=1.0, description="Requires /reload to take effect.")
    default_steps: int | None = Field(None, ge=1, le=8)
    default_guidance_scale: float | None = Field(None, ge=0.0, le=5.0)
    default_width: int | None = Field(None, ge=512, le=1536)
    default_height: int | None = Field(None, ge=512, le=1536)
    default_negative_prompt: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    vram_gb: float | None = None


class ReadyResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> HealthResponse:
    vram: float | None = None
    if torch.cuda.is_available():
        vram = round(torch.cuda.memory_allocated() / 1024**3, 2)
    return HealthResponse(status="ok", model_loaded=_pipe is not None, vram_gb=vram)


@app.get("/ready")
def ready() -> ReadyResponse:
    if _pipe is not None:
        return ReadyResponse(status="ready")
    raise HTTPException(status_code=503, detail="Model loading")


@app.get("/config")
def get_config() -> ConfigResponse:
    """Return current tunable defaults."""
    return ConfigResponse(
        impressionism_strength=_IMPRESSIONISM_STRENGTH,
        default_steps=_DEFAULT_STEPS,
        default_guidance_scale=_DEFAULT_GUIDANCE_SCALE,
        default_width=_DEFAULT_WIDTH,
        default_height=_DEFAULT_HEIGHT,
        default_negative_prompt=_DEFAULT_NEGATIVE_PROMPT,
    )


@app.post("/config")
def update_config(update: ConfigUpdate) -> ConfigResponse:
    """Update tunable defaults at runtime. Changes to impressionism_strength require POST /reload."""
    global _IMPRESSIONISM_STRENGTH, _DEFAULT_STEPS, _DEFAULT_GUIDANCE_SCALE
    global _DEFAULT_WIDTH, _DEFAULT_HEIGHT, _DEFAULT_NEGATIVE_PROMPT

    if update.impressionism_strength is not None:
        _IMPRESSIONISM_STRENGTH = update.impressionism_strength
        logger.info("Updated impressionism_strength=%.2f (POST /reload to apply)", _IMPRESSIONISM_STRENGTH)
    if update.default_steps is not None:
        _DEFAULT_STEPS = update.default_steps
    if update.default_guidance_scale is not None:
        _DEFAULT_GUIDANCE_SCALE = update.default_guidance_scale
    if update.default_width is not None:
        _DEFAULT_WIDTH = update.default_width
    if update.default_height is not None:
        _DEFAULT_HEIGHT = update.default_height
    if update.default_negative_prompt is not None:
        _DEFAULT_NEGATIVE_PROMPT = update.default_negative_prompt

    logger.info("Config updated: steps=%d cfg=%.1f size=%dx%d",
                _DEFAULT_STEPS, _DEFAULT_GUIDANCE_SCALE, _DEFAULT_WIDTH, _DEFAULT_HEIGHT)
    return get_config()


@app.post("/reload")
def reload_pipeline() -> dict[str, str]:
    """Re-load and re-fuse LoRAs with current impressionism_strength. Takes ~30s."""
    global _pipe
    logger.info("Reloading pipeline with impressionism_strength=%.2f ...", _IMPRESSIONISM_STRENGTH)
    _pipe = None
    torch.cuda.empty_cache()
    gc.collect()
    _load()
    return {"status": "reloaded", "impressionism_strength": str(_IMPRESSIONISM_STRENGTH)}


@app.get("/debug")
def debug_info() -> dict:
    """Detailed pipeline debug info — LoRA state, scheduler, VRAM, torch config."""
    info: dict = {
        "cuda_available": torch.cuda.is_available(),
        "torch_version": torch.__version__,
        "pipeline_loaded": _pipe is not None,
    }

    if torch.cuda.is_available():
        info["gpu"] = {
            "name": torch.cuda.get_device_name(0),
            "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1024**3, 3),
            "vram_reserved_gb": round(torch.cuda.memory_reserved() / 1024**3, 3),
            "vram_total_gb": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2),
            "max_vram_allocated_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        }

    if _pipe is not None:
        # Scheduler info
        sched_cfg = dict(_pipe.scheduler.config) if hasattr(_pipe.scheduler, "config") else {}
        info["scheduler"] = {
            "type": type(_pipe.scheduler).__name__,
            "timestep_spacing": sched_cfg.get("timestep_spacing"),
            "num_train_timesteps": sched_cfg.get("num_train_timesteps"),
        }
        # Pipeline dtype
        info["dtype"] = str(_pipe.dtype)
        # LoRA info
        info["lora"] = {
            "impressionism_path": _IMPRESSIONISM_LORA_PATH,
            "impressionism_exists": os.path.exists(_IMPRESSIONISM_LORA_PATH),
            "impressionism_strength": _IMPRESSIONISM_STRENGTH,
            "lightning_repo": _LIGHTNING_REPO,
            "lightning_file": _LIGHTNING_LORA,
            "impressionism_loaded": _load_state["impressionism_loaded"],
            "lightning_loaded": _load_state["lightning_loaded"],
            "fused": _load_state["fused"],
            "load_error": _load_state["load_error"],
            "load_time_s": _load_state["load_time_s"],
        }
        # UNet info
        if hasattr(_pipe, "unet"):
            unet = _pipe.unet
            info["unet"] = {
                "dtype": str(unet.dtype),
                "num_parameters_M": round(sum(p.numel() for p in unet.parameters()) / 1e6, 1),
                "device": str(next(unet.parameters()).device),
            }

    info["config"] = {
        "impressionism_strength": _IMPRESSIONISM_STRENGTH,
        "default_steps": _DEFAULT_STEPS,
        "default_guidance_scale": _DEFAULT_GUIDANCE_SCALE,
        "default_width": _DEFAULT_WIDTH,
        "default_height": _DEFAULT_HEIGHT,
        "default_negative_prompt": _DEFAULT_NEGATIVE_PROMPT,
    }

    return info


@app.post(
    "/generate",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Generated PNG image"}},
)
def generate(request: GenerateRequest) -> Response:
    """Generate an impressionist painting and return it as PNG bytes."""
    pipe = _load()

    seed = request.seed if request.seed is not None else int(torch.randint(0, 2**32, (1,)).item())
    generator = torch.Generator(device="cuda").manual_seed(seed)

    logger.info(
        "Generating: steps=%d size=%dx%d cfg=%.1f seed=%d",
        request.steps,
        request.width,
        request.height,
        request.guidance_scale,
        seed,
    )

    t0 = time.perf_counter()

    # Clear VRAM fragmentation
    torch.cuda.empty_cache()

    try:
        with torch.inference_mode():
            result = pipe(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                num_inference_steps=request.steps,
                guidance_scale=request.guidance_scale,
                generator=generator,
            )
    except RuntimeError as exc:
        torch.cuda.empty_cache()
        gc.collect()
        if "out of memory" in str(exc).lower():
            logger.error("CUDA OOM during generation: %s", exc)
            raise HTTPException(status_code=507, detail="GPU out of memory") from exc
        raise

    torch.cuda.empty_cache()

    img = result.images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    elapsed = time.perf_counter() - t0
    logger.info(
        "Done in %.1fs. VRAM: %.2f GB",
        elapsed,
        torch.cuda.memory_allocated() / 1024**3,
    )

    return Response(
        content=buf.read(),
        media_type="image/png",
        headers={"X-Seed": str(seed), "X-Elapsed": f"{elapsed:.2f}"},
    )


@app.get("/viewer", response_class=HTMLResponse, include_in_schema=False)
def viewer() -> str:
    """Browser-based live rendering viewer."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Image Server v2 — Live Viewer</title>
<style>
  body { font-family: monospace; background: #111; color: #eee; margin: 0; padding: 20px; }
  h1 { font-size: 1.2em; margin-bottom: 16px; }
  form { display: flex; flex-direction: column; gap: 8px; max-width: 640px; }
  label { font-size: 0.85em; color: #aaa; }
  input, textarea { background: #222; border: 1px solid #444; color: #eee;
                    padding: 6px 8px; font-family: monospace; font-size: 0.9em; border-radius: 4px; }
  textarea { resize: vertical; height: 60px; }
  .row { display: flex; gap: 8px; }
  .row input { width: 80px; }
  button { align-self: flex-start; padding: 8px 20px; background: #2a6; border: none;
           color: #fff; font-size: 0.95em; cursor: pointer; border-radius: 4px; }
  button:disabled { background: #555; cursor: not-allowed; }
  #status { margin-top: 12px; font-size: 0.8em; color: #8af; min-height: 1.2em; }
  #canvas-wrap { margin-top: 16px; }
  #preview { max-width: 100%; border: 1px solid #333; border-radius: 4px; display: none; }
</style>
</head>
<body>
<h1>Image Server v2 — Live Viewer</h1>
<form id="form">
  <label>Prompt</label>
  <textarea name="prompt" required>a dark forest at night, moonlight through the trees</textarea>
  <label>Negative prompt</label>
  <input name="negative_prompt" value="photorealistic, photograph, blurry, low quality, watermark">
  <div class="row">
    <div><label>Width</label><input name="width" type="number" value="1280"></div>
    <div><label>Height</label><input name="height" type="number" value="720"></div>
    <div><label>Steps</label><input name="steps" type="number" value="4" min="1" max="8"></div>
    <div><label>CFG</label><input name="guidance_scale" type="number" step="0.1" value="2.0"></div>
    <div><label>Seed</label><input name="seed" type="number" placeholder="random"></div>
  </div>
  <button type="submit" id="btn">Generate</button>
</form>
<div id="status">Ready.</div>
<div id="canvas-wrap"><img id="preview" alt="preview"></div>
<script>
const form = document.getElementById('form');
const btn = document.getElementById('btn');
const status = document.getElementById('status');
const preview = document.getElementById('preview');
let es = null;

form.addEventListener('submit', e => {
  e.preventDefault();
  if (es) { es.close(); es = null; }

  const fd = new FormData(form);
  const body = {
    prompt: fd.get('prompt'),
    negative_prompt: fd.get('negative_prompt'),
    width: parseInt(fd.get('width')),
    height: parseInt(fd.get('height')),
    steps: parseInt(fd.get('steps')),
    guidance_scale: parseFloat(fd.get('guidance_scale')),
  };
  const seed = fd.get('seed');
  if (seed) body.seed = parseInt(seed);

  btn.disabled = true;
  status.textContent = 'Starting…';
  preview.style.display = 'none';

  fetch('/generate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify(body),
  }).then(res => {
    if (!res.ok) { status.textContent = 'Error: ' + res.status; btn.disabled = false; return; }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    function read() {
      reader.read().then(({ done, value }) => {
        if (done) { btn.disabled = false; return; }
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const msg = JSON.parse(line.slice(6));
            if (msg.error) { status.textContent = 'Error: ' + msg.error; btn.disabled = false; return; }
            const fmt = msg.final ? 'PNG' : 'JPEG';
            preview.src = 'data:image/' + fmt.toLowerCase() + ';base64,' + msg.image;
            preview.style.display = 'block';
            status.textContent = msg.final
              ? `Done — seed ${msg.seed} — ${msg.elapsed}s`
              : `Step ${msg.step}/${msg.total}…`;
          } catch {}
        }
        read();
      });
    }
    read();
  }).catch(err => { status.textContent = 'Error: ' + err; btn.disabled = false; });
});
</script>
</body>
</html>"""


@app.post(
    "/generate/stream",
    summary="Generate (streaming)",
    description=(
        "Stream intermediate denoising steps as SSE events.\n\n"
        "Each event is a JSON object with `step`, `total`, `image` (base64 JPEG).\n"
        "The final event adds `final: true`, `seed`, `elapsed`, and sends a base64 PNG.\n\n"
        "**Tip:** for a live visual preview open [/viewer](/viewer) instead of using Swagger."
    ),
)
async def generate_stream(request: GenerateRequest) -> StreamingResponse:
    """Stream each denoising step as an SSE event with a base64-encoded JPEG preview."""
    pipe = _load()

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    seed = request.seed if request.seed is not None else int(torch.randint(0, 2**32, (1,)).item())
    t0 = time.perf_counter()

    def _decode_latents(latents: torch.Tensor) -> str:
        """VAE-decode latents to a base64 JPEG string."""
        scaled = latents / pipe.vae.config.scaling_factor  # type: ignore[attr-defined]
        decoded = pipe.vae.decode(scaled.to(torch.float16), return_dict=False)[0]
        decoded = (decoded / 2 + 0.5).clamp(0, 1)
        arr = (decoded.cpu().permute(0, 2, 3, 1).float().numpy() * 255).round().astype("uint8")[0]
        pil = Image.fromarray(arr)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode()

    def _run() -> None:
        generator = torch.Generator(device="cuda").manual_seed(seed)
        torch.cuda.empty_cache()

        def _callback(
            _pipe: StableDiffusionXLPipeline,
            step_index: int,
            _timestep: torch.Tensor,
            callback_kwargs: dict,  # type: ignore[type-arg]
        ) -> dict:  # type: ignore[type-arg]
            latents: torch.Tensor | None = callback_kwargs.get("latents")
            if latents is not None:
                try:
                    b64 = _decode_latents(latents)
                    payload = json.dumps({"step": step_index + 1, "total": request.steps, "image": b64})
                    loop.call_soon_threadsafe(queue.put_nowait, payload)
                except Exception as exc:
                    logger.warning("Step decode failed: %s", exc)
            return callback_kwargs

        try:
            with torch.inference_mode():
                result = pipe(
                    prompt=request.prompt,
                    negative_prompt=request.negative_prompt,
                    width=request.width,
                    height=request.height,
                    num_inference_steps=request.steps,
                    guidance_scale=request.guidance_scale,
                    generator=generator,
                    callback_on_step_end=_callback,
                )
            img = result.images[0]
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_final = base64.b64encode(buf.getvalue()).decode()
            elapsed = round(time.perf_counter() - t0, 2)
            payload = json.dumps({
                "step": request.steps,
                "total": request.steps,
                "final": True,
                "seed": seed,
                "elapsed": elapsed,
                "image": b64_final,
            })
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except RuntimeError as exc:
            torch.cuda.empty_cache()
            gc.collect()
            err = json.dumps({"error": str(exc)})
            loop.call_soon_threadsafe(queue.put_nowait, err)
        finally:
            torch.cuda.empty_cache()
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_run, daemon=True).start()

    async def _event_gen() -> AsyncIterator[str]:
        while True:
            item = await queue.get()
            if item is None:
                return
            yield f"data: {item}\n\n"

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8006, reload=False)
