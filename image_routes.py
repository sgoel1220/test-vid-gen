"""FastAPI route handlers for image generation."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

import engine as tts_engine
from config import config_manager, get_output_path
from enums import ImageStyle, JobStatus
from image.engine import (
    generate_image,
    is_image_model_loaded,
    load_image_model,
    unload_image_model,
)
from image.models import (
    ChunkBasedImageGenRequest,
    ChunkGroupPreviewResponse,
    ImageGenRequest,
    ImageGenResponse,
    ImageJobCreatedResponse,
    ImageJobStatusResponse,
    ManualImageGenRequest,
    ManualImageGenResponse,
    PromptPreviewRequest,
    SavedImageArtifact,
    ScenePrompt,
)
from image.chunk_grouper import group_chunks_for_images
from image.prompts import chunk_groups_to_scene_prompts, extract_scene_prompts
from text.normalization import _unload_model as unload_qwen_model
from job_store import job_store
from text.chunking import sanitize_filename

logger = logging.getLogger(__name__)

image_router = APIRouter(prefix="/api/images", tags=["images"])


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _img_cfg(key: str, default):
    """Shorthand for reading image_generation.* config values."""
    return config_manager.get(f"image_generation.{key}", default)


def _resolve_steps(request: ImageGenRequest) -> int:
    if request.steps is not None:
        return request.steps
    return int(_img_cfg("default_steps", 30))


def _resolve_guidance(request: ImageGenRequest) -> float:
    if request.guidance_scale is not None:
        return request.guidance_scale
    return float(_img_cfg("default_guidance_scale", 7.5))


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------

def _ensure_image_model(device: str = "cuda") -> None:
    if not is_image_model_loaded():
        model_id = str(_img_cfg("model_id", "Tongyi-MAI/Z-Image-Turbo"))
        ok = load_image_model(device=device, model_id=model_id)
        if not ok:
            raise RuntimeError("Failed to load Z-Image pipeline.")


def _make_run_dir(label: Optional[str] = None) -> tuple[str, Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = uuid.uuid4().hex[:8]
    safe_label = sanitize_filename(label) if label else "image_gen"
    run_id = f"{ts}__{safe_label}__{tag}"
    base = get_output_path(ensure_absolute=True) / "image_gen_runs" / run_id
    base.mkdir(parents=True, exist_ok=True)
    return run_id, base


def _execute_image_gen(
    request: ImageGenRequest,
    progress_callback=None,
) -> ImageGenResponse:
    """Core orchestration: extract prompts → generate images → save artifacts."""
    warnings: List[str] = []

    # 1. Always unload TTS to free VRAM — SDXL needs ~6.5GB and most pods
    #    can't hold both models simultaneously.
    tts_was_loaded = tts_engine.is_model_ready()
    if tts_was_loaded:
        logger.info("Unloading TTS model to free VRAM for SDXL…")
        tts_engine.unload_model()
        warnings.append("TTS model was unloaded to free VRAM for image generation. It will reload automatically after.")

    # 2. Resolve prompts
    if request.manual_prompts:
        scenes = request.manual_prompts
    else:
        scenes = extract_scene_prompts(
            story_text=request.story_text,
            num_scenes=request.num_scenes,
            style=request.style,
        )
    if not scenes:
        raise ValueError("No scene prompts could be extracted from the story text.")

    # 2b. Unload Qwen to free VRAM before loading SDXL
    unload_qwen_model()

    # 3. Ensure SDXL is loaded (first run downloads ~6.5GB)
    if progress_callback:
        progress_callback(completed=0, total=0, message="Loading SDXL model (first run downloads ~6.5GB)…")
    device = config_manager.get_string("tts_engine.device", "cuda")
    _ensure_image_model(device=device)

    steps = _resolve_steps(request)
    guidance = _resolve_guidance(request)
    run_id, run_dir = _make_run_dir(request.run_label)
    total = len(scenes)

    images: List[SavedImageArtifact] = []
    for i, scene in enumerate(scenes):
        if progress_callback:
            progress_callback(completed=i, total=total, message=f"Generating image {i + 1}/{total}…")

        seed = request.seed + i if request.seed is not None else None
        logger.info("Generating image %d/%d (seed=%s)…", i + 1, total, seed)

        pil_img = generate_image(
            prompt=scene.prompt,
            negative_prompt=scene.negative_prompt,
            width=request.width,
            height=request.height,
            steps=steps,
            guidance_scale=guidance,
            seed=seed,
        )

        filename = f"scene_{i:03d}.png"
        filepath = run_dir / filename
        pil_img.save(filepath, format="PNG")

        rel_path = f"image_gen_runs/{run_id}/{filename}"
        images.append(
            SavedImageArtifact(
                filename=filename,
                relative_path=rel_path,
                url=f"/outputs/{rel_path}",
                width=request.width,
                height=request.height,
                prompt_used=scene.prompt,
                negative_prompt_used=scene.negative_prompt,
                seed_used=seed if seed is not None else -1,
            )
        )

    if progress_callback:
        progress_callback(completed=total, total=total, message="Finalizing…")

    # 4. Unload SDXL and reload TTS so the server is ready for speech again
    logger.info("Unloading SDXL pipeline after image generation…")
    unload_image_model()
    if tts_was_loaded:
        logger.info("Reloading TTS model after image generation…")
        # Small delay to let GC free SDXL VRAM before TTS loads (single GPU constraint)
        import time
        time.sleep(2)
        tts_engine.start_background_model_load()

    # 5. Write manifest
    manifest_name = "manifest.json"
    response = ImageGenResponse(
        run_id=run_id,
        output_dir=str(run_dir),
        scenes=scenes,
        images=images,
        manifest_relative_path=f"image_gen_runs/{run_id}/{manifest_name}",
        manifest_url=f"/outputs/image_gen_runs/{run_id}/{manifest_name}",
        warnings=warnings,
    )
    manifest_path = run_dir / manifest_name
    manifest_path.write_text(
        json.dumps(jsonable_encoder(response), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return response


def _run_image_job(job_id: str, request: ImageGenRequest) -> None:
    """Background worker for async image gen jobs."""
    try:
        job_store.update(job_id, status=JobStatus.RUNNING, message="Starting image generation…")

        def _progress(completed: int, total: int, message: str = ""):
            job_store.update(
                job_id,
                progress_completed=completed,
                progress_total=total,
                message=message or (f"Generating image {completed + 1}/{total}…" if completed < total else "Finalizing…"),
            )

        result = _execute_image_gen(request, progress_callback=_progress)
        job_store.update(
            job_id,
            status=JobStatus.COMPLETED,
            message="Completed",
            result=result.model_dump(),
        )
    except Exception as exc:
        logger.error("Image gen job %s failed: %s", job_id, exc, exc_info=True)
        job_store.update(
            job_id,
            status=JobStatus.FAILED,
            message="Failed",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@image_router.post("/generate", response_model=ImageGenResponse)
async def generate_images(request: ImageGenRequest) -> ImageGenResponse:
    """Synchronous image generation — blocks until all images are saved."""
    try:
        return _execute_image_gen(request)
    except Exception as exc:
        logger.error("Image generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@image_router.post("/jobs", response_model=ImageJobCreatedResponse)
async def create_image_job(request: ImageGenRequest) -> ImageJobCreatedResponse:
    """Create an async image generation job."""
    job_id = uuid.uuid4().hex
    job_store.create(job_id)
    threading.Thread(target=_run_image_job, args=(job_id, request), daemon=True).start()
    return ImageJobCreatedResponse(
        job_id=job_id,
        status_url=f"/api/images/jobs/{job_id}",
    )


@image_router.get("/jobs/{job_id}", response_model=ImageJobStatusResponse)
async def get_image_job(job_id: str) -> ImageJobStatusResponse:
    """Poll an async image gen job."""
    raw = job_store.get_raw(job_id)
    return ImageJobStatusResponse.model_validate(raw)


@image_router.post("/prompts/preview", response_model=list[ScenePrompt])
async def preview_prompts(request: PromptPreviewRequest) -> list[ScenePrompt]:
    """Preview extracted scene prompts without generating images."""
    try:
        scenes = extract_scene_prompts(
            story_text=request.story_text,
            num_scenes=request.num_scenes,
            style=request.style,
        )
        if not scenes:
            raise HTTPException(status_code=400, detail="No scenes could be extracted.")
        return scenes
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Prompt preview failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Manual single-image generation
# ---------------------------------------------------------------------------

@image_router.post("/manual", response_model=ManualImageGenResponse)
async def generate_manual_image(request: ManualImageGenRequest) -> ManualImageGenResponse:
    """Generate a single image from a manual prompt — no LLM scene extraction."""
    try:
        # Unload TTS if loaded (single GPU constraint)
        tts_was_loaded = tts_engine.is_model_ready()
        if tts_was_loaded:
            logger.info("Unloading TTS model to free VRAM for Z-Image…")
            tts_engine.unload_model()

        # Ensure Z-Image is loaded
        device = config_manager.get_string("tts_engine.device", "cuda")
        _ensure_image_model(device=device)

        steps = request.steps or int(_img_cfg("default_steps", 9))
        guidance = request.guidance_scale if request.guidance_scale is not None else float(_img_cfg("default_guidance_scale", 0.0))
        run_id, run_dir = _make_run_dir(request.run_label or "manual")

        pil_img = generate_image(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            width=request.width,
            height=request.height,
            steps=steps,
            guidance_scale=guidance,
            seed=request.seed,
        )

        filename = "image.png"
        filepath = run_dir / filename
        pil_img.save(filepath, format="PNG")

        rel_path = f"image_gen_runs/{run_id}/{filename}"
        artifact = SavedImageArtifact(
            filename=filename,
            relative_path=rel_path,
            url=f"/outputs/{rel_path}",
            width=request.width,
            height=request.height,
            prompt_used=request.prompt,
            negative_prompt_used=request.negative_prompt,
            seed_used=request.seed if request.seed is not None else -1,
        )

        # Write manifest
        manifest_name = "manifest.json"
        response = ManualImageGenResponse(
            run_id=run_id,
            output_dir=str(run_dir),
            image=artifact,
            manifest_url=f"/outputs/image_gen_runs/{run_id}/{manifest_name}",
        )
        manifest_path = run_dir / manifest_name
        manifest_path.write_text(
            json.dumps(jsonable_encoder(response), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Unload Z-Image and reload TTS
        unload_image_model()
        if tts_was_loaded:
            import time
            time.sleep(2)
            tts_engine.start_background_model_load()

        return response

    except Exception as exc:
        logger.error("Manual image generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Chunk-based image generation (post-TTS workflow)
# ---------------------------------------------------------------------------

@image_router.post("/chunks/preview", response_model=ChunkGroupPreviewResponse)
async def preview_chunk_groups(request: ChunkBasedImageGenRequest) -> ChunkGroupPreviewResponse:
    """Preview how TTS chunks will be grouped for background images."""
    try:
        chunk_groups = group_chunks_for_images(
            chunks=request.chunks,
            chunks_per_group=request.chunks_per_group,
        )
        if not chunk_groups:
            raise HTTPException(status_code=400, detail="No chunk groups could be created.")

        return ChunkGroupPreviewResponse(
            chunk_groups=[
                {
                    "group_index": g.group_index,
                    "chunk_indices": g.chunk_indices,
                    "background_description": g.background_description,
                    "num_chunks": len(g.chunk_indices),
                }
                for g in chunk_groups
            ],
            total_groups=len(chunk_groups),
            chunks_processed=len(request.chunks),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Chunk group preview failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@image_router.post("/chunks/generate", response_model=ImageGenResponse)
async def generate_images_from_chunks(request: ChunkBasedImageGenRequest) -> ImageGenResponse:
    """Generate background images from TTS chunks (synchronous).

    This is the primary workflow for creepy pasta videos:
    1. TTS generates audio chunks
    2. LLM groups 5-6 chunks together based on scene/setting
    3. LLM generates background-only scene descriptions (no humans)
    4. SDXL generates painting-style background images
    """
    try:
        # Step 1: Group chunks using LLM
        chunk_groups = group_chunks_for_images(
            chunks=request.chunks,
            chunks_per_group=request.chunks_per_group,
        )
        if not chunk_groups:
            raise ValueError("No chunk groups could be created from the provided chunks.")

        # Step 2: Convert chunk groups to scene prompts
        scene_prompts = chunk_groups_to_scene_prompts(
            chunk_groups=chunk_groups,
            style=request.style,
        )

        # Step 3: Build a modified ImageGenRequest for execution
        image_request = ImageGenRequest(
            story_text="",  # Not used when manual_prompts provided
            num_scenes=len(scene_prompts),
            style=request.style,
            width=request.width,
            height=request.height,
            steps=request.steps,
            guidance_scale=request.guidance_scale,
            seed=request.seed,
            manual_prompts=scene_prompts,
            run_label=request.run_label or "chunk_based",
        )

        # Step 4: Execute image generation
        return _execute_image_gen(image_request)

    except Exception as exc:
        logger.error("Chunk-based image generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@image_router.post("/chunks/jobs", response_model=ImageJobCreatedResponse)
async def create_chunk_image_job(request: ChunkBasedImageGenRequest) -> ImageJobCreatedResponse:
    """Create an async chunk-based image generation job."""
    try:
        # Build ImageGenRequest from chunk-based request
        chunk_groups = group_chunks_for_images(
            chunks=request.chunks,
            chunks_per_group=request.chunks_per_group,
        )
        if not chunk_groups:
            raise ValueError("No chunk groups could be created.")

        scene_prompts = chunk_groups_to_scene_prompts(
            chunk_groups=chunk_groups,
            style=request.style,
        )

        image_request = ImageGenRequest(
            story_text="",
            num_scenes=len(scene_prompts),
            style=request.style,
            width=request.width,
            height=request.height,
            steps=request.steps,
            guidance_scale=request.guidance_scale,
            seed=request.seed,
            manual_prompts=scene_prompts,
            run_label=request.run_label or "chunk_based",
        )

        job_id = uuid.uuid4().hex
        job_store.create(job_id)
        threading.Thread(target=_run_image_job, args=(job_id, image_request), daemon=True).start()

        return ImageJobCreatedResponse(
            job_id=job_id,
            status_url=f"/api/images/jobs/{job_id}",
        )
    except Exception as exc:
        logger.error("Failed to create chunk-based image job: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
