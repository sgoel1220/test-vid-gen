"""sfx_generation step executor.

Pipeline:
  1. Check generate_sfx flag — skip if false
  2. Get chunk texts from workflow_chunks (created by tts_synthesis step)
  3. Group chunks into scenes using group_chunks_into_scenes()
  4. Generate SFX cue lists via LLM for each scene (BEFORE GPU spin-up)
  5. Resume check: skip (scene, cue) pairs with existing SFX_AUDIO blobs
  6. Spin up sfx-server GPU pod
  7. For each pending cue:
     a. POST /generate { prompt, duration_sec } → WAV bytes
     b. Save WAV blob to Postgres with scene/cue metadata in mime_type
  8. Terminate sfx pod
  9. Return SfxGenerationStepOutput

GPU pod contract (stateless /generate endpoint):
  POST /generate
  Body: { prompt: str, duration_sec: float, seed: int, guidance_scale: float, ddim_steps: int }
  Response: WAV bytes (Content-Type: audio/wav), HTTP 200 on success

Resume strategy:
  SFX_AUDIO blobs are stored with mime_type="audio/wav;s={scene};c={cue}" to enable
  per-cue resume. On retry, existing (scene, cue) pairs are skipped.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.engine import StepContext
from app.engine.models import SkippedStepOutput
from app.gpu import GpuPodSpec
from app.gpu.lifecycle import workflow_gpu_pod
from app.llm.audio_design import SfxCue, generate_sfx_cues
from app.models.enums import BlobType
from app.models.json_schemas import WorkflowInputSchema
from app.models.workflow import WorkflowBlob
from app.services import blob_service
from app.services.workflow_service import get_chunks_for_image_step, get_optional_workflow_id
from app.text.scene_grouping import Scene, group_chunks_into_scenes
from app.workflows.db_helpers import get_session_maker

log = logging.getLogger(__name__)

_GENERATE_PATH = "/generate"
_GENERATE_TIMEOUT_SEC = 120  # 2 minutes per SFX clip

# WAV RIFF magic bytes
_WAV_MAGIC = b"RIFF"

# Mime type prefix used to encode scene/cue metadata for resume support
# Full format: "audio/wav;s={scene_index};c={cue_index};h={desc_hash}"
# desc_hash is the first 8 hex chars of SHA-256(description+position+duration_sec)
# so that blobs from a different LLM cue plan are never silently reused on resume.
_SFX_MIME_PREFIX = "audio/wav;s="


def _cue_hash(description: str, position: str, duration_sec: float) -> str:
    """Return the first 8 hex chars of SHA-256(description|position|duration_sec).

    Used as a stable cue identity embedded in the blob mime_type so that resume
    can detect when the LLM generated a different cue plan and skip blob reuse.
    """
    payload = f"{description}|{position}|{duration_sec}"
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class SfxClipResult(BaseModel):
    """Result of generating a single SFX clip for a scene cue."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0, description="Zero-based scene index")
    cue_index: int = Field(ge=0, description="Zero-based cue index within the scene")
    description: str = Field(description="Natural-language description of the sound effect")
    blob_id: str = Field(description="UUID of the saved WAV blob")
    duration_sec: float = Field(gt=0, description="Duration of the SFX clip in seconds")
    position: Literal["beginning", "middle", "end"] = Field(
        description="Where in the scene this cue plays"
    )


class SfxGenerationStepOutput(BaseModel):
    """Output of the sfx_generation step."""

    model_config = ConfigDict(extra="forbid")

    step_type: Literal["sfx_generation"] = "sfx_generation"
    pod_id: str = Field(description="GPU pod ID used for generation (or 'resumed')")
    clip_count: int = Field(ge=0, description="Total number of SFX clips generated")
    clips: list[SfxClipResult] = Field(description="SFX clip results ordered by scene/cue index")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sfx_pod_spec() -> GpuPodSpec:
    """Create GpuPodSpec for the sfx-server pod."""
    return GpuPodSpec(
        gpu_type=settings.gpu_type,
        image=settings.sfx_server_image,
        disk_size_gb=settings.gpu_container_disk_gb,
        volume_gb=settings.gpu_volume_gb,
        ports=[settings.sfx_server_port],
        cloud_type=settings.gpu_cloud_type,
    )


def _make_sfx_mime(
    scene_index: int, cue_index: int, description: str, position: str, duration_sec: float
) -> str:
    """Encode scene/cue indices and cue hash into mime_type string for resume tracking.

    Format: "audio/wav;s={si};c={ci};h={hash8}" (max ~31 chars, fits String(50)).
    The hash is the first 8 hex chars of SHA-256(description|position|duration_sec),
    which lets resume skip blobs whose cue identity no longer matches the current plan.
    """
    h = _cue_hash(description, position, duration_sec)
    return f"audio/wav;s={scene_index};c={cue_index};h={h}"


def _parse_sfx_mime(mime_type: str) -> tuple[int, int, str] | None:
    """Parse (scene_index, cue_index, desc_hash) from an SFX mime_type string.

    Returns None if the mime_type does not match the SFX format.
    """
    if not mime_type.startswith(_SFX_MIME_PREFIX):
        return None
    try:
        # "audio/wav;s=2;c=3;h=abcd1234" → parts = ["audio/wav","s=2","c=3","h=abcd1234"]
        parts = mime_type.split(";")
        scene_index = int(parts[1].split("=")[1])
        cue_index = int(parts[2].split("=")[1])
        desc_hash = parts[3].split("=")[1]
        return scene_index, cue_index, desc_hash
    except (IndexError, ValueError):
        return None


def _validate_wav_response(resp: httpx.Response) -> bytes:
    """Validate that the HTTP response contains valid WAV audio data.

    Args:
        resp: The HTTP response from the sfx-server.

    Returns:
        The WAV bytes if valid.

    Raises:
        ValueError: If the response is not valid WAV audio.
    """
    content_type = resp.headers.get("content-type", "")
    if "audio/wav" not in content_type and "audio/x-wav" not in content_type:
        raise ValueError(f"Expected audio/wav content type, got: {content_type!r}")

    wav_bytes: bytes = resp.content
    if not wav_bytes:
        raise ValueError("Empty response body from sfx-server")

    if not wav_bytes.startswith(_WAV_MAGIC):
        raise ValueError(
            f"Response does not start with RIFF magic bytes, got: {wav_bytes[:4].hex()!r}"
        )

    return wav_bytes


async def _get_existing_sfx_blobs(
    session_maker: async_sessionmaker[AsyncSession],
    workflow_id: uuid.UUID,
) -> dict[tuple[int, int, str], uuid.UUID]:
    """Query existing SFX_AUDIO blobs and build a (scene, cue, hash) → blob_id mapping.

    Uses the scene/cue indices and cue description hash encoded in the blob mime_type
    for exact, hash-verified per-cue resume. Blobs with a different hash are not reused,
    preventing stale blobs from being mapped to a different LLM-generated cue.

    Args:
        session_maker: SQLAlchemy async session factory.
        workflow_id: Workflow UUID to filter blobs by.

    Returns:
        Dict mapping (scene_index, cue_index, desc_hash) to blob UUID for already-generated clips.
        Only blobs whose mime_type encodes a matching hash are eligible for resume.
    """
    async with session_maker() as session:
        result = await session.execute(
            select(WorkflowBlob.id, WorkflowBlob.mime_type)
            .where(
                WorkflowBlob.workflow_id == workflow_id,
                WorkflowBlob.blob_type == BlobType.SFX_AUDIO,
            )
            .order_by(WorkflowBlob.created_at)
        )
        rows = result.all()

    # Key: (scene_index, cue_index, desc_hash) → blob_id
    existing: dict[tuple[int, int, str], uuid.UUID] = {}
    for blob_id, mime_type in rows:
        parsed = _parse_sfx_mime(mime_type)
        if parsed is not None:
            existing[parsed] = blob_id
    return existing


async def _generate_scene_cues(
    scenes: list[Scene],
    workflow_id: uuid.UUID,
) -> list[tuple[Scene, list[SfxCue]]]:
    """Generate SFX cue lists for all scenes via LLM before GPU spin-up.

    Runs all LLM work before the GPU pod starts to minimize expensive GPU time.

    Args:
        scenes: All scenes to generate cues for.
        workflow_id: Workflow UUID for LLM usage tracking.

    Returns:
        List of (scene, cues) pairs in scene order.
    """
    from app.llm.client import set_llm_workflow_context

    set_llm_workflow_context(workflow_id)
    try:
        results: list[tuple[Scene, list[SfxCue]]] = []
        for scene in scenes:
            cue_result = await generate_sfx_cues(scene.combined_text)
            log.info(
                "sfx scene %d: generated %d cues",
                scene.scene_index,
                len(cue_result.cues),
            )
            results.append((scene, cue_result.cues))
        return results
    finally:
        set_llm_workflow_context(None)


async def _generate_sfx_clips(
    endpoint_url: str,
    pending: list[tuple[Scene, int, SfxCue]],
    workflow_id: uuid.UUID,
    session_maker: async_sessionmaker[AsyncSession],
) -> list[SfxClipResult]:
    """Generate SFX clips for pending (scene, cue_index, cue) triples.

    POSTs each cue description to the sfx-server, validates the WAV response,
    and stores each clip as a BlobType.SFX_AUDIO blob with scene/cue metadata
    encoded in the mime_type for resume support.

    Args:
        endpoint_url: Base URL of the ready sfx GPU pod.
        pending: List of (scene, cue_index, cue) triples to generate.
        workflow_id: Workflow UUID for blob FK.
        session_maker: SQLAlchemy async session factory.

    Returns:
        List of SfxClipResult with blob IDs.
    """
    clips: list[SfxClipResult] = []

    async with httpx.AsyncClient(
        base_url=endpoint_url, timeout=_GENERATE_TIMEOUT_SEC
    ) as client:
        for scene, cue_index, cue in pending:
            resp = await client.post(
                _GENERATE_PATH,
                json={
                    "prompt": cue.description,
                    "duration_sec": cue.duration_sec,
                },
            )
            resp.raise_for_status()
            wav_bytes = _validate_wav_response(resp)

            async with session_maker() as session:
                blob = await blob_service.store(
                    session=session,
                    data=wav_bytes,
                    mime_type=_make_sfx_mime(scene.scene_index, cue_index, cue.description, cue.position, cue.duration_sec),
                    blob_type=BlobType.SFX_AUDIO,
                    workflow_id=workflow_id,
                )
                await session.commit()

            clips.append(
                SfxClipResult(
                    scene_index=scene.scene_index,
                    cue_index=cue_index,
                    description=cue.description,
                    blob_id=str(blob.id),
                    duration_sec=cue.duration_sec,
                    position=cue.position,
                )
            )
            log.info(
                "sfx scene %d cue %d done blob_id=%s",
                scene.scene_index,
                cue_index,
                blob.id,
            )

    return clips


# ---------------------------------------------------------------------------
# Step entry point
# ---------------------------------------------------------------------------


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> SfxGenerationStepOutput | SkippedStepOutput:
    """Generate SFX clips for each scene using a sfx-server GPU pod.

    Pipeline:
      1. Check generate_sfx flag — return SkippedStepOutput if false
      2. Get chunks from DB, group into scenes
      3. Generate SFX cue lists via LLM (before GPU)
      4. Resume check: find already-generated SFX_AUDIO blobs
      5. Spin up sfx GPU pod for remaining clips
      6. Return sorted output

    Args:
        input: Validated workflow input (contains generate_sfx flag).
        ctx: Step execution context (provides workflow_run_id).

    Returns:
        SfxGenerationStepOutput with all clip results, or SkippedStepOutput if disabled.
    """
    if not input.generate_sfx:
        log.info("sfx_generation skipped: generate_sfx=false")
        return SkippedStepOutput(reason="generate_sfx=false")

    workflow_run_id: str = ctx.workflow_run_id
    workflow_id_uuid = get_optional_workflow_id(workflow_run_id)

    if workflow_id_uuid is None:
        raise ValueError(
            f"workflow_run_id={workflow_run_id!r} is not a valid UUID; "
            "sfx_generation requires DB tracking"
        )

    log.info("sfx_generation started workflow_id=%s", workflow_run_id)

    session_maker = get_session_maker()

    # --- 2. Get chunks from DB ---
    async with session_maker() as session:
        chunk_data = await get_chunks_for_image_step(session, workflow_id_uuid)

    if not chunk_data:
        raise ValueError(
            f"No chunks found for workflow {workflow_run_id!r}; "
            "tts_synthesis step may not have completed"
        )

    # --- 3. Group chunks into scenes ---
    scenes = group_chunks_into_scenes(
        chunks=[c.text for c in chunk_data],
        chunks_per_scene=settings.chunks_per_scene,
    )
    log.info(
        "sfx_generation: %d scenes from %d chunks (chunks_per_scene=%d)",
        len(scenes),
        len(chunk_data),
        settings.chunks_per_scene,
    )

    # --- 4. Generate cue plans via LLM (before GPU spin-up) ---
    scene_cues = await _generate_scene_cues(scenes, workflow_id_uuid)
    total_cues = sum(len(cues) for _, cues in scene_cues)
    log.info(
        "sfx_generation: %d total cues across %d scenes",
        total_cues,
        len(scene_cues),
    )

    # --- 5. Resume check: find existing SFX_AUDIO blobs ---
    existing_blobs = await _get_existing_sfx_blobs(session_maker, workflow_id_uuid)
    log.info("sfx_generation: %d existing SFX clips found (resume)", len(existing_blobs))

    # Build resumed and pending lists
    all_clips: list[SfxClipResult] = []
    pending: list[tuple[Scene, int, SfxCue]] = []  # (scene, cue_index, cue)

    for scene, cues in scene_cues:
        for cue_index, cue in enumerate(cues):
            key = (scene.scene_index, cue_index, _cue_hash(cue.description, cue.position, cue.duration_sec))
            if key in existing_blobs:
                all_clips.append(
                    SfxClipResult(
                        scene_index=scene.scene_index,
                        cue_index=cue_index,
                        description=cue.description,
                        blob_id=str(existing_blobs[key]),
                        duration_sec=cue.duration_sec,
                        position=cue.position,
                    )
                )
                log.info(
                    "sfx scene %d cue %d: resumed from DB blob_id=%s",
                    scene.scene_index,
                    cue_index,
                    existing_blobs[key],
                )
            else:
                pending.append((scene, cue_index, cue))

    # --- 6. Fully resumed? ---
    if not pending:
        log.info(
            "sfx_generation fully resumed from DB: %d clips, no pod needed",
            len(all_clips),
        )
        return SfxGenerationStepOutput(
            pod_id="resumed",
            clip_count=len(all_clips),
            clips=sorted(all_clips, key=lambda c: (c.scene_index, c.cue_index)),
        )

    log.info(
        "sfx_generation: %d/%d clips pending, %d already done",
        len(pending),
        total_cues,
        len(all_clips),
    )

    # --- 7. Spin up sfx GPU pod and generate remaining clips ---
    async with workflow_gpu_pod(
        session_maker,
        spec=_sfx_pod_spec(),
        idempotency_key=f"sfx-{workflow_run_id}",
        workflow_id=workflow_id_uuid,
        label="sfx",
        service_port=settings.sfx_server_port,
    ) as (pod, endpoint_url):
        new_clips = await _generate_sfx_clips(
            endpoint_url=endpoint_url,
            pending=pending,
            workflow_id=workflow_id_uuid,
            session_maker=session_maker,
        )

    all_clips.extend(new_clips)
    all_clips.sort(key=lambda c: (c.scene_index, c.cue_index))

    output = SfxGenerationStepOutput(
        pod_id=pod.id,
        clip_count=len(all_clips),
        clips=all_clips,
    )
    log.info(
        "sfx_generation complete clips=%d pod=%s",
        len(all_clips),
        pod.id,
    )
    return output
