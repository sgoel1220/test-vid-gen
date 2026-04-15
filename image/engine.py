"""Z-Image-Turbo image generation engine — lazy-loaded singleton, thread-safe."""

from __future__ import annotations

import gc
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded pipeline globals (module-level singleton, same pattern as engine.py).
_pipeline = None  # ZImagePipeline instance
_pipeline_device: Optional[str] = None
_pipeline_lock = threading.Lock()

_DEFAULT_MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"


def load_image_model(
    device: str = "cuda",
    model_id: str = _DEFAULT_MODEL_ID,
) -> bool:
    """Load the Z-Image pipeline. Returns True if loaded successfully."""
    global _pipeline, _pipeline_device

    with _pipeline_lock:
        if _pipeline is not None:
            logger.info("Z-Image pipeline already loaded on %s.", _pipeline_device)
            return True

    try:
        import torch as _torch
        from diffusers import ZImagePipeline

        logger.info("Downloading/loading Z-Image pipeline: %s (this may take several minutes on first run)…", model_id)

        pipe = ZImagePipeline.from_pretrained(
            model_id,
            torch_dtype=_torch.bfloat16,
            low_cpu_mem_usage=False,
        )

        logger.info("Z-Image weights loaded, moving to %s…", device)
        pipe = pipe.to(device)

        with _pipeline_lock:
            _pipeline = pipe
            _pipeline_device = device

        logger.info("Z-Image pipeline ready on %s.", device)
        return True

    except Exception as exc:
        logger.error("Failed to load Z-Image pipeline: %s", exc, exc_info=True)
        return False


def unload_image_model() -> bool:
    """Unload Z-Image pipeline and release GPU memory."""
    global _pipeline, _pipeline_device

    with _pipeline_lock:
        if _pipeline is None:
            logger.info("Z-Image pipeline not loaded — nothing to unload.")
            return True

        logger.info("Unloading Z-Image pipeline…")
        del _pipeline
        _pipeline = None
        _pipeline_device = None

    # Aggressive memory cleanup — models must swap in/out of single GPU
    gc.collect()
    gc.collect()  # Second pass to catch circular refs

    try:
        import torch as _torch
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
            _torch.cuda.synchronize()  # Wait for all CUDA ops to finish
            logger.info("CUDA memory freed: %.2f GB available", _torch.cuda.mem_get_info()[0] / 1024**3)
    except Exception as e:
        logger.warning("CUDA cleanup warning: %s", e)

    logger.info("Z-Image pipeline unloaded and GPU memory released.")
    return True


def is_image_model_loaded() -> bool:
    with _pipeline_lock:
        return _pipeline is not None


def generate_image(
    prompt: str,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 9,
    guidance_scale: float = 0.0,
    seed: Optional[int] = None,
):
    """Generate a single image. Returns a PIL.Image.Image.

    Raises RuntimeError if the pipeline is not loaded.
    """
    with _pipeline_lock:
        pipe = _pipeline

    if pipe is None:
        raise RuntimeError("Z-Image pipeline not loaded. Call load_image_model() first.")

    import torch as _torch

    generator = None
    if seed is not None:
        device = _pipeline_device or "cuda"
        generator = _torch.Generator(device=device).manual_seed(seed)

    logger.info("Running Z-Image inference (%d steps, guidance=%.1f)…", steps, guidance_scale)
    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt or None,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )

    logger.info("Z-Image inference complete.")
    return result.images[0]
