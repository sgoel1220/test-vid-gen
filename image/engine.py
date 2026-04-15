"""SDXL image generation engine — lazy-loaded singleton, thread-safe."""

from __future__ import annotations

import gc
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded pipeline globals (module-level singleton, same pattern as engine.py).
_pipeline = None  # StableDiffusionXLPipeline instance
_pipeline_device: Optional[str] = None
_pipeline_lock = threading.Lock()

_DEFAULT_MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"


def load_image_model(
    device: str = "cuda",
    model_id: str = _DEFAULT_MODEL_ID,
) -> bool:
    """Load the SDXL pipeline. Returns True if loaded successfully."""
    global _pipeline, _pipeline_device

    with _pipeline_lock:
        if _pipeline is not None:
            logger.info("SDXL pipeline already loaded on %s.", _pipeline_device)
            return True

    try:
        import torch as _torch
        from diffusers import StableDiffusionXLPipeline

        logger.info("Loading SDXL pipeline: %s on %s…", model_id, device)
        pipe = StableDiffusionXLPipeline.from_pretrained(
            model_id,
            torch_dtype=_torch.float16,
            use_safetensors=True,
            variant="fp16",
        )
        pipe = pipe.to(device)
        pipe.enable_attention_slicing()

        with _pipeline_lock:
            _pipeline = pipe
            _pipeline_device = device

        logger.info("SDXL pipeline loaded successfully on %s.", device)
        return True

    except Exception as exc:
        logger.error("Failed to load SDXL pipeline: %s", exc, exc_info=True)
        return False


def unload_image_model() -> bool:
    """Unload SDXL pipeline and release GPU memory."""
    global _pipeline, _pipeline_device

    with _pipeline_lock:
        if _pipeline is None:
            logger.info("SDXL pipeline not loaded — nothing to unload.")
            return True

        logger.info("Unloading SDXL pipeline…")
        del _pipeline
        _pipeline = None
        _pipeline_device = None

    gc.collect()

    try:
        import torch as _torch
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
    except Exception:
        pass

    logger.info("SDXL pipeline unloaded and GPU memory released.")
    return True


def is_image_model_loaded() -> bool:
    with _pipeline_lock:
        return _pipeline is not None


def generate_image(
    prompt: str,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 30,
    guidance_scale: float = 7.5,
    seed: Optional[int] = None,
):
    """Generate a single image. Returns a PIL.Image.Image.

    Raises RuntimeError if the pipeline is not loaded.
    """
    with _pipeline_lock:
        pipe = _pipeline

    if pipe is None:
        raise RuntimeError("SDXL pipeline not loaded. Call load_image_model() first.")

    import torch as _torch

    generator = None
    if seed is not None:
        device = _pipeline_device or "cuda"
        generator = _torch.Generator(device=device).manual_seed(seed)

    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt or None,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )

    return result.images[0]
