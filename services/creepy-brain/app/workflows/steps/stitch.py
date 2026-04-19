"""stitch_final step executor.

Pulls WAV chunk blobs from Postgres, stitches locally, encodes to MP3,
optionally creates video with images using ffmpeg.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import SkippedStepOutput, StepContext

from app.audio.encoding import encode_wav_to_mp3
from app.models.enums import BlobType, ChunkStatus
from app.models.json_schemas import ImageGenerationStepOutput, WorkflowInputSchema
from app.models.workflow import WorkflowBlob
from app.services import blob_service
from app.services.workflow_service import (
    ChunkForImageStep,
    get_chunks_for_image_step,
    get_optional_workflow_id,
    get_scenes_for_workflow,
)
from app.workflows.db_helpers import get_session_maker
from app.workflows.steps.image import SceneImageResult

log = structlog.get_logger(__name__)

# ffmpeg video constants
_VIDEO_SCALE = "1280:720"  # output dimensions (W:H)


class StitchStepOutput(BaseModel):
    """Output of the stitch_final step."""

    model_config = ConfigDict(extra="forbid")

    final_audio_blob_id: str = Field(description="UUID of the final MP3 blob")
    final_video_blob_id: str | None = Field(
        default=None, description="UUID of the final video blob (if created)"
    )
    chunk_count: int = Field(ge=0, description="Number of audio chunks stitched")
    total_duration_sec: float = Field(ge=0, description="Total audio duration in seconds")


async def execute(input: WorkflowInputSchema, ctx: StepContext) -> StitchStepOutput | SkippedStepOutput:
    """Stitch audio chunks and optionally create video with images.

    Supports resume: if final audio/video blobs already exist for this
    workflow, returns existing results without re-encoding.

    Args:
        input: Validated workflow input (contains stitch_video flag).
        ctx: step execution context (provides workflow_run_id and parent outputs).

    Returns:
        Pydantic output model, or skipped output if stitching is disabled.
    """
    # Early return if stitching disabled
    if not input.stitch_video:
        log.info("stitch_final skipped: stitch_video=False")
        return SkippedStepOutput(reason="stitch_video=False")

    workflow_run_id: str = ctx.workflow_run_id
    workflow_id = get_optional_workflow_id(workflow_run_id)

    if workflow_id is None:
        raise ValueError(
            f"workflow_run_id={workflow_run_id} is not a valid UUID; "
            "stitch_final requires DB tracking"
        )

    log.info("stitch_final started workflow_id=%s", workflow_run_id)

    # Get parent step outputs
    image_output = ctx.get_parent_output("image_generation", ImageGenerationStepOutput)

    session_maker = get_session_maker()

    # --- Resume check: look for existing final blobs (defer data to avoid loading MB) ---
    async with session_maker() as session:
        existing_blobs_result = await session.execute(
            select(
                WorkflowBlob.id,
                WorkflowBlob.blob_type,
            ).where(
                WorkflowBlob.workflow_id == workflow_id,
                WorkflowBlob.blob_type.in_([BlobType.FINAL_AUDIO, BlobType.FINAL_VIDEO]),
            )
        )
        existing_blobs = {row.blob_type: row.id for row in existing_blobs_result.all()}

    existing_audio_id: uuid.UUID | None = existing_blobs.get(BlobType.FINAL_AUDIO)
    existing_video_id: uuid.UUID | None = existing_blobs.get(BlobType.FINAL_VIDEO)

    # If both exist (or audio exists and no video needed), return early
    needs_video = image_output is not None
    if existing_audio_id is not None:
        if not needs_video or existing_video_id is not None:
            log.info(
                "stitch_final: resuming — final blobs already exist (audio=%s, video=%s)",
                existing_audio_id,
                existing_video_id,
            )
            async with session_maker() as session:
                chunk_data = await get_chunks_for_image_step(session, workflow_id)

            chunk_count = len([c for c in chunk_data if c.tts_status == ChunkStatus.COMPLETED])
            total_dur = sum(c.duration_sec or 0.0 for c in chunk_data if c.tts_status == ChunkStatus.COMPLETED)

            return StitchStepOutput(
                final_audio_blob_id=str(existing_audio_id),
                final_video_blob_id=str(existing_video_id) if existing_video_id else None,
                chunk_count=chunk_count,
                total_duration_sec=total_dur,
            )

    # --- 1. Fetch WAV chunk blobs from DB ---
    async with session_maker() as session:
        chunk_data = await get_chunks_for_image_step(session, workflow_id)

    if not chunk_data:
        raise ValueError(
            f"No chunks found for workflow {workflow_run_id}; "
            "tts_synthesis step may not have completed"
        )

    # --- Quality gate: skip non-completed chunks ---
    valid_chunks: list[ChunkForImageStep] = []
    for chunk in chunk_data:
        if chunk.tts_status != ChunkStatus.COMPLETED:
            log.warning(
                "stitch_final: skipping chunk %s (tts_status=%s)",
                chunk.index,
                chunk.tts_status,
            )
        else:
            valid_chunks.append(chunk)

    if not valid_chunks:
        raise ValueError(
            f"All {len(chunk_data)} chunks failed TTS for workflow "
            f"{workflow_run_id}; cannot stitch"
        )

    chunk_data = valid_chunks
    log.info("stitch_final: %d chunks to stitch", len(chunk_data))

    # --- 2. Decode WAV blobs and concatenate ---
    arrays: list[np.ndarray[Any, np.dtype[np.float32]]] = []
    sample_rate: int | None = None

    async with session_maker() as session:
        for chunk in chunk_data:
            blob_id_str = chunk.blob_id
            if not blob_id_str:
                raise ValueError(f"Chunk {chunk.index} has no blob_id; TTS may have failed")

            blob = await blob_service.get(session, uuid.UUID(blob_id_str))
            audio, chunk_sr = sf.read(io.BytesIO(blob.data), dtype="float32")
            arrays.append(audio)

            if sample_rate is None:
                sample_rate = chunk_sr
            elif sample_rate != chunk_sr:
                log.warning(
                    "Sample rate mismatch: expected %d, got %d for chunk %d",
                    sample_rate,
                    chunk_sr,
                    chunk.index,
                )

    if sample_rate is None:
        raise ValueError("No audio data found in chunks")

    stitched = np.concatenate(arrays)
    total_duration_sec = len(stitched) / sample_rate

    log.info(
        "stitch_final: stitched %d chunks, duration=%.1fs, sr=%d",
        len(arrays),
        total_duration_sec,
        sample_rate,
    )

    # --- 3. Encode to MP3 (skip if audio blob already exists from partial resume) ---
    if existing_audio_id is not None:
        async with session_maker() as session:
            audio_blob_obj = await blob_service.get(session, existing_audio_id)
        mp3_bytes = audio_blob_obj.data
        audio_blob_id = existing_audio_id
        log.info("stitch_final: reusing existing audio blob %s", audio_blob_id)
    else:
        mp3_bytes = await encode_wav_to_mp3(stitched, sample_rate)
        log.info("stitch_final: encoded MP3, size=%d bytes", len(mp3_bytes))

        # --- 4. Store final audio blob ---
        async with session_maker() as session:
            audio_blob = await blob_service.store(
                session=session,
                data=mp3_bytes,
                mime_type="audio/mpeg",
                blob_type=BlobType.FINAL_AUDIO,
                workflow_id=workflow_id,
            )
            await session.commit()
            audio_blob_id = audio_blob.id

        log.info("stitch_final: saved final audio blob_id=%s", audio_blob_id)

    output = StitchStepOutput(
        final_audio_blob_id=str(audio_blob_id),
        final_video_blob_id=None,
        chunk_count=len(chunk_data),
        total_duration_sec=total_duration_sec,
    )

    # --- 5. Create video if images exist ---
    if needs_video:
        # Load scenes from DB so this works on resume/fork paths where output_json may be empty.
        async with session_maker() as session:
            db_scenes = await get_scenes_for_workflow(session, workflow_id)

        # Build scene_id → chunk_indices from already-loaded chunk_data.
        scene_chunk_map: dict[str, list[int]] = {}
        for c in chunk_data:
            if c.scene_id:
                scene_chunk_map.setdefault(c.scene_id, []).append(c.index)

        completed_scenes = [sc for sc in db_scenes if sc.image_blob_id is not None]
        if not completed_scenes:
            log.warning("stitch_final: no completed scenes in DB, skipping video")
            needs_video = False

    if needs_video:
        scene_results = [
            SceneImageResult(
                scene_index=sc.scene_index,
                chunk_indices=sorted(scene_chunk_map.get(str(sc.id), [])),
                image_blob_id=str(sc.image_blob_id),
                image_prompt=sc.image_prompt or "",
                image_negative_prompt=sc.image_negative_prompt or "",
            )
            for sc in completed_scenes
        ]
        log.info(
            "stitch_final: creating video with %d scene images",
            len(scene_results),
        )

        chunk_durations: dict[int, float] = {
            c.index: (c.duration_sec or 0.0) for c in chunk_data
        }
        video_blob_id = await _create_video(
            scenes=scene_results,
            mp3_bytes=mp3_bytes,
            workflow_id=workflow_id,
            session_maker=session_maker,
            chunk_durations=chunk_durations,
        )
        output.final_video_blob_id = str(video_blob_id)
        log.info("stitch_final: saved final video blob_id=%s", video_blob_id)

    log.info(
        "stitch_final complete: audio=%s video=%s chunks=%d dur=%.1fs",
        output.final_audio_blob_id,
        output.final_video_blob_id,
        output.chunk_count,
        output.total_duration_sec,
    )

    return output


async def _create_video(
    scenes: list[SceneImageResult],
    mp3_bytes: bytes,
    workflow_id: uuid.UUID,
    session_maker: async_sessionmaker[AsyncSession],
    chunk_durations: dict[int, float],
) -> uuid.UUID:
    """Create video from scene images and audio using ffmpeg.

    Each image is displayed for the sum of its scene's chunk durations so that
    the video length matches the stitched audio exactly.

    Args:
        scenes: Scene results from image_generation step (each has image_blob_id).
        mp3_bytes: Final MP3 audio bytes.
        workflow_id: Workflow UUID for blob storage.
        session_maker: SQLAlchemy async session factory.
        chunk_durations: Mapping of chunk_index → duration in seconds.

    Returns:
        UUID of the saved video blob.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Fetch and write image files; compute per-scene durations
        scene_durations: list[float] = []
        async with session_maker() as session:
            for i, scene in enumerate(scenes):
                blob_id_str = scene.image_blob_id
                if not blob_id_str:
                    continue
                blob = await blob_service.get(session, uuid.UUID(blob_id_str))
                img_path = tmpdir_path / f"image_{i:03d}.png"
                img_path.write_bytes(blob.data)

                dur = sum(
                    chunk_durations.get(idx, 0.0) for idx in scene.chunk_indices
                )
                scene_durations.append(dur if dur > 0 else 5.0)

        # Write audio file
        mp3_path = tmpdir_path / "audio.mp3"
        mp3_path.write_bytes(mp3_bytes)

        # Build ffmpeg concat file so each image holds for its scene's duration
        concat_path = tmpdir_path / "concat.txt"
        lines: list[str] = ["ffconcat version 1.0"]
        for i, dur in enumerate(scene_durations):
            img_name = f"image_{i:03d}.png"
            lines.append(f"file '{img_name}'")
            lines.append(f"duration {dur:.6f}")
        # ffconcat requires repeating last entry without duration to avoid 1-frame overshoot
        if scene_durations:
            last_name = f"image_{len(scene_durations) - 1:03d}.png"
            lines.append(f"file '{last_name}'")
        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Create video with ffmpeg using concat demuxer (exact per-image durations)
        video_path = tmpdir_path / "final_video.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(mp3_path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            f"scale={_VIDEO_SCALE}:force_original_aspect_ratio=decrease,pad={_VIDEO_SCALE}:(ow-iw)/2:(oh-ih)/2",
            "-c:a",
            "copy",
            "-shortest",
            str(video_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg video creation failed: {stderr}")

        video_bytes = video_path.read_bytes()

    # Store video blob
    async with session_maker() as session:
        video_blob = await blob_service.store(
            session=session,
            data=video_bytes,
            mime_type="video/mp4",
            blob_type=BlobType.FINAL_VIDEO,
            workflow_id=workflow_id,
        )
        await session.commit()

    return video_blob.id
