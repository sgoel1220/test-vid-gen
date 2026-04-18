"""image_generation step executor.

Pipeline:
  1. Check generate_images flag — skip if false
  2. Get chunk texts from workflow_chunks (created by tts_synthesis step)
  3. Group chunks into scenes using group_chunks_into_scenes()
  4. Generate prompts via LLM and SAVE TO DB (before GPU spin-up)
     - Create WorkflowScene records with prompt/negative_prompt
     - Link chunks to their scenes via scene_id FK
  5. Spin up image GPU pod
  6. Wait for pod ready
  7. For each scene (prompts already saved):
     a. POST /generate { prompt, negative_prompt, width, height } → PNG bytes
     b. Save PNG blob to Postgres
     c. Update scene with image_blob_id
  8. Terminate image pod
  9. Return scene image blob IDs with chunk mapping

GPU pod contract (stateless /generate endpoint):
  POST /generate
  Body: { prompt: str, negative_prompt: str, width: int, height: int }
  Response: PNG bytes (Content-Type: image/png), HTTP 200 always
"""

from __future__ import annotations

import logging
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.engine import SkippedStepOutput, StepContext

import app.db as _db
from app.config import settings
from app.gpu import GpuPodSpec, get_provider
from app.gpu.lifecycle import terminate_and_finalize
from app.llm.image_prompts import generate_scene_image_prompt
from app.models.enums import BlobType, ChunkStatus, GpuProvider as GpuProviderEnum
from app.models.schemas import WorkflowInputSchema
from app.models.workflow import WorkflowScene
from app.services import blob_service
from app.services.cost_service import CostService
from app.services.workflow_service import (
    ChunkForImageStep,
    WorkflowService,
    get_chunks_for_image_step,
    get_optional_workflow_id,
    get_scenes_for_workflow,
)
from app.text.scene_grouping import Scene, group_chunks_into_scenes

log = logging.getLogger(__name__)

_GENERATE_PATH = "/generate"
_GENERATE_TIMEOUT_SEC = 180  # 3 minutes per image generation

# PNG magic bytes
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _validate_png_response(resp: httpx.Response) -> bytes:
    """Validate that the response contains valid PNG image data.

    Args:
        resp: The HTTP response from the image server.

    Returns:
        The PNG bytes if valid.

    Raises:
        ValueError: If the response is not a valid PNG image.
    """
    # Check content type
    content_type = resp.headers.get("content-type", "")
    if "image/png" not in content_type:
        raise ValueError(
            f"Expected image/png content type, got: {content_type}"
        )

    # Check we have content
    png_bytes: bytes = resp.content
    if not png_bytes:
        raise ValueError("Empty response body from image server")

    # Check PNG magic bytes
    if not png_bytes.startswith(_PNG_MAGIC):
        raise ValueError(
            f"Response does not start with PNG magic bytes, got: {png_bytes[:8].hex()}"
        )

    return png_bytes


class SceneImageResult(BaseModel):
    """Result of generating an image for a single scene."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0, description="Zero-based scene index")
    chunk_indices: list[int] = Field(description="Indices of chunks in this scene")
    image_blob_id: str = Field(description="UUID of the saved PNG blob")
    image_prompt: str = Field(description="SDXL prompt used for generation")
    image_negative_prompt: str = Field(description="SDXL negative prompt used")


class ScenePrompt(BaseModel):
    """Scene with generated prompt, ready for image generation."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0, description="Zero-based scene index")
    chunk_indices: list[int] = Field(description="Indices of chunks in this scene")
    prompt: str = Field(description="SDXL positive prompt")
    negative_prompt: str = Field(description="SDXL negative prompt")


class ImageStepOutput(BaseModel):
    """Output of the image_generation step."""

    model_config = ConfigDict(extra="forbid")

    scenes: list[SceneImageResult] = Field(description="Image results per scene")
    pod_id: str = Field(description="GPU pod ID used for generation")
    scene_count: int = Field(ge=0, description="Number of scenes processed")


def _image_pod_spec() -> GpuPodSpec:
    """Create GpuPodSpec for the image server (separate from TTS)."""
    return GpuPodSpec(
        gpu_type=settings.gpu_type,
        image=settings.image_server_image,
        disk_size_gb=settings.gpu_container_disk_gb,
        volume_gb=settings.gpu_volume_gb,
        ports=[settings.image_server_port],
        cloud_type=settings.gpu_cloud_type,
    )


def _scene_from_db(db_scene: WorkflowScene, chunk_indices: list[int]) -> SceneImageResult:
    """Convert a completed DB scene to a SceneImageResult."""
    return SceneImageResult(
        scene_index=db_scene.scene_index,
        chunk_indices=chunk_indices,
        image_blob_id=str(db_scene.image_blob_id),
        image_prompt=db_scene.image_prompt or "",
        image_negative_prompt=db_scene.image_negative_prompt or "",
    )


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> ImageStepOutput | SkippedStepOutput:
    """Generate images for each scene using an image GPU pod.

    Pipeline:
      1. Check generate_images flag
      2. Get chunks from DB, group into scenes
      3. Generate prompts via LLM and save to DB (BEFORE GPU)
      4. Spin up GPU pod
      5. Generate images from saved prompts
      6. Terminate GPU pod

    Args:
        input: Validated workflow input (contains generate_images flag).
        ctx: step execution context (provides workflow_run_id).

    Returns:
        Pydantic output model, or skipped output if image generation is disabled.
    """
    # --- 1. Check generate_images flag ---
    if not input.generate_images:
        log.info("image_generation skipped: generate_images=False")
        return SkippedStepOutput(reason="generate_images=False")

    workflow_run_id: str = ctx.workflow_run_id
    workflow_id_uuid = get_optional_workflow_id(workflow_run_id)

    if workflow_id_uuid is None:
        raise ValueError(
            f"workflow_run_id={workflow_run_id} is not a valid UUID; "
            "image_generation requires DB tracking"
        )

    log.info("image_generation started workflow_id=%s", workflow_run_id)

    # --- 2. Get chunk texts from DB ---
    session_maker = _db.async_session_maker
    assert session_maker is not None, (
        "DB not initialized — call init_db() before starting"
    )
    async with session_maker() as session:
        chunk_data = await get_chunks_for_image_step(session, workflow_id_uuid)
        existing_scenes = await get_scenes_for_workflow(session, workflow_id_uuid)

    if not chunk_data:
        raise ValueError(
            f"No chunks found for workflow {workflow_run_id}; "
            "tts_synthesis step may not have completed"
        )

    chunk_texts: list[str] = [c.text for c in chunk_data]
    log.info("image_generation: %d chunks to group into scenes", len(chunk_texts))

    # --- 3. Group chunks into scenes ---
    scenes = group_chunks_into_scenes(
        chunks=chunk_texts, chunks_per_scene=settings.chunks_per_scene
    )
    log.info(
        "image_generation: %d scenes (chunks_per_scene=%d)",
        len(scenes),
        settings.chunks_per_scene,
    )

    # Build lookup for existing scenes (resume support)
    existing_scene_map: dict[int, WorkflowScene] = {
        s.scene_index: s for s in existing_scenes
    }

    # --- 4. Check for completed scenes (resume) and pending scenes ---
    resumed_results: list[SceneImageResult] = []
    pending_scenes: list[Scene] = []

    for scene in scenes:
        db_scene = existing_scene_map.get(scene.scene_index)
        if db_scene is not None and db_scene.image_status == ChunkStatus.COMPLETED and db_scene.image_blob_id:
            # Scene already completed
            resumed_results.append(_scene_from_db(db_scene, scene.chunk_indices))
        else:
            pending_scenes.append(scene)

    if not pending_scenes:
        log.info(
            "image_generation fully resumed from DB: %d scenes, no pod needed",
            len(resumed_results),
        )
        return ImageStepOutput(
            scenes=resumed_results,
            pod_id="resumed",
            scene_count=len(resumed_results),
        )

    log.info(
        "image_generation: %d/%d scenes need generation, %d already done",
        len(pending_scenes),
        len(scenes),
        len(resumed_results),
    )

    # --- 5. Generate prompts and save to DB (BEFORE GPU spin-up) ---
    scene_prompts = await _generate_and_save_prompts(
        scenes=pending_scenes,
        workflow_id=workflow_id_uuid,
        existing_scene_map=existing_scene_map,
    )
    log.info("image_generation: %d prompts generated and saved", len(scene_prompts))

    # --- 6. Spin up image GPU pod ---
    provider = get_provider(settings.runpod_api_key)
    pod = await provider.create_pod(
        spec=_image_pod_spec(),
        idempotency_key=f"img-{workflow_run_id}",
    )
    log.info("image pod created pod_id=%s provider=%s", pod.id, pod.provider)

    # Persist pod to DB for cost tracking
    session_maker = _db.async_session_maker
    assert session_maker is not None
    async with session_maker() as session:
        cost_svc = CostService(session)
        await cost_svc.record_pod(
            pod_id=pod.id,
            provider=GpuProviderEnum(pod.provider),
            workflow_id=workflow_id_uuid,
            gpu_type=pod.gpu_type,
            cost_per_hour_cents=pod.cost_per_hour_cents,
        )

    # --- 7. Wait for pod ready, then generate images ---
    try:
        pod = await provider.wait_for_ready(
            pod.id,
            timeout_sec=settings.pod_ready_timeout_sec,
            service_port=settings.image_server_port,
        )
        assert pod.endpoint_url is not None, f"pod {pod.id} ready but has no endpoint_url"
        log.info("image pod ready endpoint=%s", pod.endpoint_url)

        # Mark pod ready for cost tracking (start billing clock)
        async with session_maker() as session:
            await CostService(session).mark_ready(pod.id, pod.endpoint_url)

        new_results = await _generate_images_from_prompts(
            endpoint_url=pod.endpoint_url,
            scene_prompts=scene_prompts,
            workflow_id=workflow_id_uuid,
        )
    finally:
        # --- 8. Terminate image pod ---
        try:
            await terminate_and_finalize(provider, pod.id, session_maker)
        except Exception as term_exc:
            log.error("failed to terminate image pod %s: %s", pod.id, term_exc)

    scene_results = sorted(
        resumed_results + new_results, key=lambda r: r.scene_index
    )
    output = ImageStepOutput(
        scenes=scene_results,
        pod_id=pod.id,
        scene_count=len(scene_results),
    )

    log.info(
        "image_generation complete scenes=%d pod=%s",
        len(scene_results),
        pod.id,
    )

    return output


async def _generate_and_save_prompts(
    scenes: list[Scene],
    workflow_id: uuid.UUID,
    existing_scene_map: dict[int, WorkflowScene],
) -> list[ScenePrompt]:
    """Generate image prompts for pending scenes and save to DB.

    This runs BEFORE GPU spin-up to minimize expensive GPU time.
    Creates WorkflowScene records and links chunks to them.

    Args:
        scenes: List of Scene objects that need prompt generation.
        workflow_id: Workflow UUID for DB FK.
        existing_scene_map: Map of scene_index to existing WorkflowScene (for resume).

    Returns:
        List of ScenePrompt with prompts ready for image generation.
    """
    scene_prompts: list[ScenePrompt] = []

    session_maker = _db.async_session_maker
    assert session_maker is not None

    for scene in scenes:
        # Check if scene already has a prompt (partial resume)
        existing = existing_scene_map.get(scene.scene_index)
        if existing is not None and existing.image_prompt:
            # Use existing prompt
            scene_prompts.append(
                ScenePrompt(
                    scene_index=scene.scene_index,
                    chunk_indices=scene.chunk_indices,
                    prompt=existing.image_prompt,
                    negative_prompt=existing.image_negative_prompt or "",
                )
            )
            log.info("scene %d: using existing prompt from DB", scene.scene_index)
            continue

        # Generate prompt via LLM
        prompt_result = await generate_scene_image_prompt(scene.combined_text)
        log.info(
            "scene %d: generated prompt (%d chars)",
            scene.scene_index,
            len(prompt_result.prompt),
        )

        # Create or update scene record
        async with session_maker() as session:
            svc = WorkflowService(session)

            # Create scene and link chunks
            await svc.get_or_create_scene(
                workflow_id=workflow_id,
                scene_index=scene.scene_index,
                chunk_indices=scene.chunk_indices,
            )

            # Save prompt
            await svc.save_scene_prompt(
                workflow_id=workflow_id,
                scene_index=scene.scene_index,
                image_prompt=prompt_result.prompt,
                image_negative_prompt=prompt_result.negative_prompt,
            )
            await session.commit()

        scene_prompts.append(
            ScenePrompt(
                scene_index=scene.scene_index,
                chunk_indices=scene.chunk_indices,
                prompt=prompt_result.prompt,
                negative_prompt=prompt_result.negative_prompt,
            )
        )

        log.info(
            "scene %d: created with %d chunks, prompt saved",
            scene.scene_index,
            len(scene.chunk_indices),
        )

    return scene_prompts


async def _generate_images_from_prompts(
    endpoint_url: str,
    scene_prompts: list[ScenePrompt],
    workflow_id: uuid.UUID,
) -> list[SceneImageResult]:
    """Generate images for scenes using pre-saved prompts.

    Args:
        endpoint_url: Base URL of the ready image GPU pod.
        scene_prompts: List of ScenePrompt with prompts already saved to DB.
        workflow_id: Workflow UUID for DB FK.

    Returns:
        List of SceneImageResult with blob IDs.
    """
    scene_results: list[SceneImageResult] = []

    session_maker = _db.async_session_maker
    assert session_maker is not None

    async with httpx.AsyncClient(
        base_url=endpoint_url, timeout=_GENERATE_TIMEOUT_SEC
    ) as client:
        for sp in scene_prompts:
            # POST /generate → PNG bytes
            resp = await client.post(
                _GENERATE_PATH,
                json={
                    "prompt": sp.prompt,
                    "negative_prompt": sp.negative_prompt,
                    "width": settings.image_width,
                    "height": settings.image_height,
                },
            )
            resp.raise_for_status()
            png_bytes = _validate_png_response(resp)

            # Save PNG blob and update scene
            async with session_maker() as session:
                blob = await blob_service.store(
                    session=session,
                    data=png_bytes,
                    mime_type="image/png",
                    blob_type=BlobType.IMAGE,
                    workflow_id=workflow_id,
                )

                svc = WorkflowService(session)
                await svc.complete_scene_image(
                    workflow_id=workflow_id,
                    scene_index=sp.scene_index,
                    blob_id=blob.id,
                )
                await session.commit()

            scene_results.append(
                SceneImageResult(
                    scene_index=sp.scene_index,
                    chunk_indices=sp.chunk_indices,
                    image_blob_id=str(blob.id),
                    image_prompt=sp.prompt,
                    image_negative_prompt=sp.negative_prompt,
                )
            )

            log.info(
                "scene %d/%d done blob_id=%s chunks=%s",
                sp.scene_index + 1,
                len(scene_prompts),
                blob.id,
                sp.chunk_indices,
            )

    return scene_results
