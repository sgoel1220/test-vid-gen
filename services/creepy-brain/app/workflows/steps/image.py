"""image_generation step executor.

Pipeline:
  1. Check generate_images flag — skip if false
  2. Get chunk texts from workflow_chunks (created by tts_synthesis step)
  3. Group chunks into scenes using group_chunks_into_scenes()
  4. Spin up image GPU pod (separate from TTS pod)
  5. Wait for pod ready
  6. For each scene:
     a. Generate image prompt via generate_scene_image_prompt(scene.combined_text)
     b. POST /generate { prompt, negative_prompt, width, height } → PNG bytes
     c. Save PNG blob to Postgres
     d. Update all chunks in the scene with the same image_blob_id and image_prompt
  7. Terminate image pod
  8. Return scene image blob IDs with chunk mapping

GPU pod contract (stateless /generate endpoint):
  POST /generate
  Body: { prompt: str, negative_prompt: str, width: int, height: int }
  Response: PNG bytes (Content-Type: image/png), HTTP 200 always
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from hatchet_sdk import Context
from pydantic import BaseModel, ConfigDict, Field

import app.db as _db
from app.config import settings
from app.gpu import GpuPodSpec, get_provider
from app.llm.image_prompts import generate_scene_image_prompt
from app.models.enums import BlobType
from app.models.schemas import WorkflowInputSchema
from app.services import blob_service
from app.services.workflow_service import (
    WorkflowService,
    get_chunks_for_image_step,
    get_optional_workflow_id,
)
from app.text.scene_grouping import group_chunks_into_scenes

log = logging.getLogger(__name__)

_GENERATE_PATH = "/generate"
_POD_TIMEOUT_SEC = 300  # 5 minutes to wait for pod ready
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
    png_bytes = resp.content
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


async def execute(
    input: WorkflowInputSchema, ctx: Context
) -> dict[str, object]:
    """Generate images for each scene using an image GPU pod.

    Args:
        input: Validated workflow input (contains generate_images flag).
        ctx: Hatchet execution context (provides workflow_run_id).

    Returns:
        dict with keys: scenes, pod_id, scene_count (or skipped/reason if skipped)
    """
    # --- 1. Check generate_images flag ---
    if not input.generate_images:
        log.info("image_generation skipped: generate_images=False")
        return {"skipped": True, "reason": "generate_images=False"}

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
        "DB not initialized — call init_db() before starting the Hatchet worker"
    )
    async with session_maker() as session:
        chunk_data = await get_chunks_for_image_step(session, workflow_id_uuid)

    if not chunk_data:
        raise ValueError(
            f"No chunks found for workflow {workflow_run_id}; "
            "tts_synthesis step may not have completed"
        )

    chunk_texts: list[str] = [str(c["text"]) for c in chunk_data]
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

    # --- 4. Spin up image GPU pod ---
    provider = get_provider(settings.runpod_api_key)
    pod = await provider.create_pod(
        spec=_image_pod_spec(),
        idempotency_key=f"img-{workflow_run_id}",
    )
    log.info("image pod created pod_id=%s provider=%s", pod.id, pod.provider)

    # --- 5. Wait for pod ready, then generate all scene images ---
    try:
        pod = await provider.wait_for_ready(
            pod.id,
            timeout_sec=_POD_TIMEOUT_SEC,
            service_port=settings.image_server_port,
        )
        assert pod.endpoint_url is not None, f"pod {pod.id} ready but has no endpoint_url"
        log.info("image pod ready endpoint=%s", pod.endpoint_url)

        scene_results = await _generate_all_scene_images(
            endpoint_url=pod.endpoint_url,
            scenes=scenes,
            workflow_id=workflow_id_uuid,
        )
    finally:
        # --- 7. Terminate image pod ---
        try:
            await provider.terminate_pod(pod.id)
            log.info("image pod terminated pod_id=%s", pod.id)
        except Exception as term_exc:
            log.error("failed to terminate image pod %s: %s", pod.id, term_exc)

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

    # Return as dict for Hatchet serialization
    return output.model_dump()


async def _generate_all_scene_images(
    endpoint_url: str,
    scenes: list[Any],  # list[Scene] from scene_grouping
    workflow_id: uuid.UUID,
) -> list[SceneImageResult]:
    """Generate images for all scenes and persist to Postgres.

    Args:
        endpoint_url: Base URL of the ready image GPU pod.
        scenes: List of Scene objects from group_chunks_into_scenes.
        workflow_id: Workflow UUID for DB FK.

    Returns:
        List of SceneImageResult with blob IDs and prompts.
    """
    scene_results: list[SceneImageResult] = []

    async with httpx.AsyncClient(
        base_url=endpoint_url, timeout=_GENERATE_TIMEOUT_SEC
    ) as client:
        for scene in scenes:
            # --- 6a. Generate image prompt via LLM ---
            prompt_result = await generate_scene_image_prompt(scene.combined_text)
            log.info(
                "scene %d: generated prompt (%d chars)",
                scene.scene_index,
                len(prompt_result.prompt),
            )

            # --- 6b. POST /generate → PNG bytes ---
            resp = await client.post(
                _GENERATE_PATH,
                json={
                    "prompt": prompt_result.prompt,
                    "negative_prompt": prompt_result.negative_prompt,
                    "width": settings.image_width,
                    "height": settings.image_height,
                },
            )
            resp.raise_for_status()
            png_bytes = _validate_png_response(resp)

            # --- 6c. Save PNG blob to Postgres ---
            session_maker = _db.async_session_maker
            assert session_maker is not None
            async with session_maker() as session:
                blob = await blob_service.store(
                    session=session,
                    data=png_bytes,
                    mime_type="image/png",
                    blob_type=BlobType.IMAGE,
                    workflow_id=workflow_id,
                )

                # --- 6d. Update all chunks in scene with same image_blob_id ---
                svc = WorkflowService(session)
                for chunk_idx in scene.chunk_indices:
                    await svc.complete_chunk_image(
                        workflow_id=workflow_id,
                        chunk_index=chunk_idx,
                        blob_id=blob.id,
                        image_prompt=prompt_result.prompt,
                    )
                await session.commit()

            scene_results.append(
                SceneImageResult(
                    scene_index=scene.scene_index,
                    chunk_indices=scene.chunk_indices,
                    image_blob_id=str(blob.id),
                    image_prompt=prompt_result.prompt,
                )
            )

            log.info(
                "scene %d/%d done blob_id=%s chunks=%s",
                scene.scene_index + 1,
                len(scenes),
                blob.id,
                scene.chunk_indices,
            )

    return scene_results
