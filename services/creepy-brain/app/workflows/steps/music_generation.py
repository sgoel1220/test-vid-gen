"""music_generation step executor.

Pipeline:
  1. Check music_params.enabled flag — return SkippedStepOutput if false
  2. Get chunk texts from workflow_chunks (created by tts_synthesis step)
  3. Group chunks into scenes using group_chunks_into_scenes()
  4. Resume check: skip scenes with existing MUSIC_AUDIO blobs
  5. Generate music mood prompts via LLM for pending scenes (before GPU spin-up)
  6. Spin up music GPU pod
  7. Scene 0: POST /generate { prompt, duration_sec } → WAV bytes
  8. Scenes 1-N: POST /outpaint { prompt, duration_sec, audio_tail_b64 } → WAV bytes
  9. Store each segment as BlobType.MUSIC_AUDIO blob
  10. Crossfade + concatenate all segments into full-length music bed
  11. Store concatenated bed as BlobType.MUSIC_BED blob

GPU pod contract (stateless endpoints):
  POST /generate
  Body: { prompt: str, duration_sec: float }
  Response: WAV bytes (Content-Type: audio/wav)

  POST /outpaint
  Body: { prompt: str, duration_sec: float, audio_tail_b64: str }
    audio_tail_b64 = base64-encoded WAV of the last _TAIL_SEC seconds of previous segment
  Response: WAV bytes (Content-Type: audio/wav)
"""

from __future__ import annotations

import base64
import io
import logging
import uuid

import httpx
import numpy as np
import soundfile as sf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import SkippedStepOutput, StepContext
from app.config import settings
from app.gpu import GpuPodSpec
from app.gpu.lifecycle import workflow_gpu_pod
from app.llm.audio_design import MusicMoodResult, generate_music_mood
from app.models.enums import BlobType
from app.models.json_schemas import (
    MusicGenerationStepOutput,
    MusicSegmentResult,
    WorkflowInputSchema,
)
from app.models.workflow import WorkflowBlob
from app.services import blob_service
from app.services.workflow_service import (
    get_chunks_for_image_step,
    get_optional_workflow_id,
)
from app.text.scene_grouping import Scene, group_chunks_into_scenes
from app.workflows.db_helpers import get_session_maker

log = logging.getLogger(__name__)

_GENERATE_PATH = "/generate"
_OUTPAINT_PATH = "/outpaint"
_MUSIC_TIMEOUT_SEC = 300.0  # 5 minutes per segment
_TAIL_SEC = 5.0  # seconds of previous segment used as continuation context
_CROSSFADE_SEC = 0.5  # crossfade overlap between adjacent segments
_DEFAULT_SCENE_DURATION_SEC = 30.0  # fallback when TTS durations are unavailable


# ---------------------------------------------------------------------------
# Pod spec
# ---------------------------------------------------------------------------


def _music_pod_spec() -> GpuPodSpec:
    """Create GpuPodSpec for the music server."""
    return GpuPodSpec(
        gpu_type=settings.gpu_type,
        image=settings.music_server_image,
        disk_size_gb=settings.gpu_container_disk_gb,
        volume_gb=settings.gpu_volume_gb,
        ports=[settings.music_server_port],
        cloud_type=settings.gpu_cloud_type,
    )


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _scene_duration(
    scene: Scene,
    chunk_duration_by_idx: dict[int, float | None],
) -> float:
    """Sum TTS durations for chunks in *scene*, falling back to default."""
    total = sum(
        chunk_duration_by_idx.get(ci) or 0.0 for ci in scene.chunk_indices
    )
    return total if total > 0.0 else _DEFAULT_SCENE_DURATION_SEC


def _validate_wav_response(resp: httpx.Response) -> bytes:
    """Validate the HTTP response contains WAV audio data.

    Args:
        resp: HTTP response from music server.

    Returns:
        WAV bytes if valid.

    Raises:
        ValueError: If the response is not valid WAV audio.
    """
    content_type = resp.headers.get("content-type", "")
    if "audio/wav" not in content_type and "audio/x-wav" not in content_type:
        raise ValueError(f"Expected audio/wav content type, got: {content_type}")
    wav_bytes: bytes = resp.content
    if not wav_bytes:
        raise ValueError("Empty response from music server")
    if not wav_bytes.startswith(b"RIFF"):
        raise ValueError(
            f"Response does not start with RIFF header, got: {wav_bytes[:4]!r}"
        )
    return wav_bytes


def _extract_tail_b64(audio_bytes: bytes, tail_sec: float) -> str:
    """Extract the last *tail_sec* seconds from a WAV as base64-encoded WAV.

    Args:
        audio_bytes: Full WAV audio bytes.
        tail_sec: Seconds to extract from the end.

    Returns:
        Base64-encoded WAV string suitable for the /outpaint endpoint.
    """
    buf = io.BytesIO(audio_bytes)
    data, sample_rate = sf.read(buf, dtype="float32")
    tail_samples = int(tail_sec * sample_rate)
    tail_data: np.ndarray = data[-tail_samples:] if len(data) > tail_samples else data

    out_buf = io.BytesIO()
    sf.write(out_buf, tail_data, sample_rate, format="WAV", subtype="PCM_16")
    return base64.b64encode(out_buf.getvalue()).decode("utf-8")


def _crossfade_and_concat(
    segment_bytes_list: list[bytes],
    crossfade_sec: float = _CROSSFADE_SEC,
) -> bytes:
    """Crossfade-overlap-add and concatenate multiple WAV segments.

    Args:
        segment_bytes_list: One WAV bytes entry per segment, in order.
        crossfade_sec: Duration of the linear fade overlap between segments.

    Returns:
        WAV bytes of the full concatenated music bed.

    Raises:
        ValueError: If the list is empty.
    """
    if not segment_bytes_list:
        raise ValueError("No segments to concatenate")

    arrays: list[np.ndarray] = []
    sample_rate: int | None = None

    for seg_bytes in segment_bytes_list:
        data, sr = sf.read(io.BytesIO(seg_bytes), dtype="float32")
        if sample_rate is None:
            sample_rate = sr
        elif sr != sample_rate:
            log.warning(
                "Music segment sample rate mismatch: expected %d, got %d — proceeding anyway",
                sample_rate,
                sr,
            )
        arrays.append(data.copy())

    if sample_rate is None:
        raise ValueError("No audio data loaded")

    if len(arrays) == 1:
        out_buf = io.BytesIO()
        sf.write(out_buf, arrays[0], sample_rate, format="WAV", subtype="PCM_16")
        return out_buf.getvalue()

    crossfade_samples = int(crossfade_sec * sample_rate)
    result: np.ndarray = arrays[0]

    for nxt in arrays[1:]:
        actual_fade = min(crossfade_samples, len(result), len(nxt))
        if actual_fade > 0:
            fade_out = np.linspace(1.0, 0.0, actual_fade, dtype=np.float32)
            fade_in = np.linspace(0.0, 1.0, actual_fade, dtype=np.float32)
            # Handle stereo: broadcast over channel dimension
            if result.ndim > 1:
                fade_out = fade_out[:, np.newaxis]
                fade_in = fade_in[:, np.newaxis]
            result[-actual_fade:] = result[-actual_fade:] * fade_out
            nxt_head = nxt[:actual_fade] * fade_in
            overlap = result[-actual_fade:] + nxt_head
            result = np.concatenate([result[:-actual_fade], overlap, nxt[actual_fade:]])
        else:
            result = np.concatenate([result, nxt])

    out_buf = io.BytesIO()
    sf.write(out_buf, result, sample_rate, format="WAV", subtype="PCM_16")
    return out_buf.getvalue()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _query_music_bed_id(
    session_maker: async_sessionmaker[AsyncSession],
    workflow_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the most recent MUSIC_BED blob ID for this workflow, or None.

    Orders by created_at DESC so that if multiple beds exist (e.g. from a
    partial retry that re-created the bed) we return the latest one and avoid
    a MultipleResultsFound error.
    """
    async with session_maker() as session:
        result = await session.execute(
            select(WorkflowBlob.id)
            .where(WorkflowBlob.workflow_id == workflow_id)
            .where(WorkflowBlob.blob_type == BlobType.MUSIC_BED)
            .order_by(WorkflowBlob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _query_music_segment_ids(
    session_maker: async_sessionmaker[AsyncSession],
    workflow_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Return MUSIC_AUDIO blob IDs in creation order (one per completed scene)."""
    async with session_maker() as session:
        result = await session.execute(
            select(WorkflowBlob.id)
            .where(WorkflowBlob.workflow_id == workflow_id)
            .where(WorkflowBlob.blob_type == BlobType.MUSIC_AUDIO)
            .order_by(WorkflowBlob.created_at)
        )
        return list(result.scalars().all())


async def _load_blob_bytes(
    session_maker: async_sessionmaker[AsyncSession],
    blob_id: uuid.UUID,
) -> bytes:
    """Fetch blob data bytes from DB."""
    async with session_maker() as session:
        blob = await blob_service.get(session, blob_id)
        return bytes(blob.data)


# ---------------------------------------------------------------------------
# Core step executor
# ---------------------------------------------------------------------------


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> MusicGenerationStepOutput | SkippedStepOutput:
    """Generate background music for each scene using a music GPU pod.

    Args:
        input: Validated workflow input.
        ctx: Step execution context (provides workflow_run_id).

    Returns:
        MusicGenerationStepOutput on success, or SkippedStepOutput if disabled.
    """
    # --- 1. Check enabled flag (also handled by runner via params_field) ---
    if not input.music_params.enabled:
        log.info("music_generation: skipped (music_params.enabled=False)")
        return SkippedStepOutput(reason="music_params.enabled=False")

    workflow_run_id: str = ctx.workflow_run_id
    workflow_id = get_optional_workflow_id(workflow_run_id)
    if workflow_id is None:
        raise ValueError(
            f"workflow_run_id={workflow_run_id} is not a valid UUID; "
            "music_generation requires DB tracking"
        )

    log.info("music_generation started workflow_id=%s", workflow_run_id)

    session_maker = get_session_maker()

    # --- 2. Get chunks from DB ---
    async with session_maker() as session:
        chunk_data = await get_chunks_for_image_step(session, workflow_id)

    if not chunk_data:
        raise ValueError(
            f"No chunks found for workflow {workflow_run_id}; "
            "tts_synthesis step may not have completed"
        )

    chunk_duration_by_idx: dict[int, float | None] = {
        c.index: c.duration_sec for c in chunk_data
    }
    chunk_texts: list[str] = [c.text for c in chunk_data]
    log.info("music_generation: %d chunks", len(chunk_texts))

    # --- 3. Group chunks into scenes ---
    scenes = group_chunks_into_scenes(
        chunks=chunk_texts, chunks_per_scene=settings.chunks_per_scene
    )
    log.info("music_generation: %d scenes", len(scenes))

    # --- 4. Resume check ---
    existing_bed_id = await _query_music_bed_id(session_maker, workflow_id)
    if existing_bed_id is not None:
        existing_seg_ids = await _query_music_segment_ids(session_maker, workflow_id)
        log.info(
            "music_generation: fully resumed, music bed blob %s", existing_bed_id
        )
        return MusicGenerationStepOutput(
            pod_id="resumed",
            segment_count=len(scenes),
            total_duration_sec=sum(
                _scene_duration(sc, chunk_duration_by_idx) for sc in scenes
            ),
            music_bed_blob_id=str(existing_bed_id),
            segments=[
                MusicSegmentResult(
                    scene_index=scenes[i].scene_index,
                    chunk_indices=scenes[i].chunk_indices,
                    duration_sec=_scene_duration(scenes[i], chunk_duration_by_idx),
                    music_blob_id=str(existing_seg_ids[i]),
                )
                for i in range(min(len(scenes), len(existing_seg_ids)))
            ],
        )

    existing_seg_ids_raw = await _query_music_segment_ids(session_maker, workflow_id)

    # Guard: clamp to at most len(scenes). Surplus blobs can accumulate when a
    # previous attempt generated some segments, failed, then the retry re-ran the
    # same scenes before reaching the MUSIC_BED store. The segments are ordered by
    # created_at (insert order), which matches scene order because _generate_pending_segments
    # commits them sequentially. Taking the first min(count, len(scenes)) is therefore safe
    # and consistent, and logs a warning if surplus blobs are detected.
    if len(existing_seg_ids_raw) > len(scenes):
        log.warning(
            "music_generation: found %d MUSIC_AUDIO blobs but only %d scenes — "
            "truncating to scene count (surplus likely from a prior partial retry)",
            len(existing_seg_ids_raw),
            len(scenes),
        )
    existing_seg_ids = existing_seg_ids_raw[: len(scenes)]
    resume_count = len(existing_seg_ids)
    pending_scenes = scenes[resume_count:]

    log.info(
        "music_generation: %d/%d scenes already done, %d to generate",
        resume_count,
        len(scenes),
        len(pending_scenes),
    )

    # --- 5. Generate music mood prompts for pending scenes (before GPU spin-up) ---
    from app.llm.client import set_llm_workflow_context  # noqa: PLC0415

    set_llm_workflow_context(workflow_id)
    pending_moods: list[MusicMoodResult] = []
    try:
        for scene in pending_scenes:
            mood = await generate_music_mood(scene.combined_text)
            pending_moods.append(mood)
            log.info(
                "music_generation: scene %d mood prompt generated (intensity=%d)",
                scene.scene_index,
                mood.intensity,
            )
    finally:
        set_llm_workflow_context(None)

    # Load continuation tail from last existing segment (partial resume)
    previous_wav: bytes | None = None
    if resume_count > 0:
        previous_wav = await _load_blob_bytes(session_maker, existing_seg_ids[-1])

    # --- 6-9. Spin up GPU, generate, store segments ---
    async with workflow_gpu_pod(
        session_maker,
        spec=_music_pod_spec(),
        idempotency_key=f"music-{workflow_run_id}",
        workflow_id=workflow_id,
        label="music",
        service_port=settings.music_server_port,
    ) as (pod, endpoint_url):
        new_seg_results, new_seg_wav = await _generate_pending_segments(
            endpoint_url=endpoint_url,
            pending_scenes=pending_scenes,
            pending_moods=pending_moods,
            chunk_duration_by_idx=chunk_duration_by_idx,
            workflow_id=workflow_id,
            session_maker=session_maker,
            previous_wav=previous_wav,
        )

    # --- 10. Crossfade + concatenate ---
    # Load existing segment bytes from DB for crossfade
    existing_wav_list: list[bytes] = []
    for seg_id in existing_seg_ids:
        existing_wav_list.append(await _load_blob_bytes(session_maker, seg_id))

    all_wav = existing_wav_list + new_seg_wav
    log.info("music_generation: crossfading %d segments", len(all_wav))
    music_bed_bytes = _crossfade_and_concat(all_wav)

    # --- 11. Store music bed blob ---
    async with session_maker() as session:
        bed_blob = await blob_service.store(
            session=session,
            data=music_bed_bytes,
            mime_type="audio/wav",
            blob_type=BlobType.MUSIC_BED,
            workflow_id=workflow_id,
        )
        await session.commit()

    # Build complete segment list (resumed + new)
    resumed_segments = [
        MusicSegmentResult(
            scene_index=scenes[i].scene_index,
            chunk_indices=scenes[i].chunk_indices,
            duration_sec=_scene_duration(scenes[i], chunk_duration_by_idx),
            music_blob_id=str(existing_seg_ids[i]),
        )
        for i in range(resume_count)
    ]
    all_segments = resumed_segments + new_seg_results
    total_duration = sum(s.duration_sec for s in all_segments)

    output = MusicGenerationStepOutput(
        pod_id=pod.id,
        segment_count=len(all_segments),
        total_duration_sec=total_duration,
        music_bed_blob_id=str(bed_blob.id),
        segments=all_segments,
    )
    log.info(
        "music_generation complete: %d segments, %.1fs, bed blob %s, pod %s",
        output.segment_count,
        output.total_duration_sec,
        output.music_bed_blob_id,
        pod.id,
    )
    return output


async def _generate_pending_segments(
    endpoint_url: str,
    pending_scenes: list[Scene],
    pending_moods: list[MusicMoodResult],
    chunk_duration_by_idx: dict[int, float | None],
    workflow_id: uuid.UUID,
    session_maker: async_sessionmaker[AsyncSession],
    previous_wav: bytes | None,
) -> tuple[list[MusicSegmentResult], list[bytes]]:
    """Generate music segments for pending scenes via the music GPU pod.

    Scene 0 (or the first pending scene with no prior wav) uses POST /generate.
    All subsequent scenes use POST /outpaint with the tail of the previous segment.

    Args:
        endpoint_url: Base URL of the ready music GPU pod.
        pending_scenes: Scenes that still need music generated.
        pending_moods: Corresponding music mood prompts (parallel to pending_scenes).
        chunk_duration_by_idx: Map from chunk_index to TTS duration.
        workflow_id: Workflow UUID for blob storage.
        session_maker: DB session factory.
        previous_wav: WAV bytes of the last already-done segment (for outpaint chaining),
            or None if starting from scratch.

    Returns:
        Tuple of (segment_results, segment_wav_bytes_list).
    """
    results: list[MusicSegmentResult] = []
    wav_list: list[bytes] = []

    async with httpx.AsyncClient(
        base_url=endpoint_url, timeout=_MUSIC_TIMEOUT_SEC
    ) as client:
        for scene, mood in zip(pending_scenes, pending_moods):
            duration_sec = _scene_duration(scene, chunk_duration_by_idx)

            if previous_wav is None:
                # First segment — generate from scratch
                resp = await client.post(
                    _GENERATE_PATH,
                    json={
                        "prompt": mood.prompt,
                        "duration_sec": duration_sec,
                    },
                )
            else:
                # Subsequent segment — continue from tail of previous
                audio_tail_b64 = _extract_tail_b64(previous_wav, _TAIL_SEC)
                resp = await client.post(
                    _OUTPAINT_PATH,
                    json={
                        "prompt": mood.prompt,
                        "duration_sec": duration_sec,
                        "audio_context": audio_tail_b64,
                    },
                )

            resp.raise_for_status()
            wav_bytes = _validate_wav_response(resp)

            async with session_maker() as session:
                seg_blob = await blob_service.store(
                    session=session,
                    data=wav_bytes,
                    mime_type="audio/wav",
                    blob_type=BlobType.MUSIC_AUDIO,
                    workflow_id=workflow_id,
                )
                await session.commit()

            seg_result = MusicSegmentResult(
                scene_index=scene.scene_index,
                chunk_indices=scene.chunk_indices,
                duration_sec=duration_sec,
                music_blob_id=str(seg_blob.id),
            )
            results.append(seg_result)
            wav_list.append(wav_bytes)
            previous_wav = wav_bytes

            log.info(
                "music_generation: scene %d done blob=%s duration=%.1fs",
                scene.scene_index,
                seg_blob.id,
                duration_sec,
            )

    return results, wav_list
