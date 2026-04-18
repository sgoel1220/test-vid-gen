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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import SkippedStepOutput, StepContext

from app.audio.encoding import encode_wav_to_mp3
from app.models.enums import BlobType, ChunkStatus
from app.models.schemas import WorkflowInputSchema
from app.services import blob_service
from app.services.workflow_service import ChunkForImageStep, get_optional_workflow_id
from app.workflows.db_helpers import get_session_maker

log = structlog.get_logger(__name__)

# ffmpeg video constants
_FRAMERATE = "1/5"  # 5 seconds per image
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
    image_output = ctx.parent_outputs.get("image_generation")

    # --- 1. Fetch WAV chunk blobs from DB ---
    session_maker = get_session_maker()

    async with session_maker() as session:
        from app.services.workflow_service import get_chunks_for_image_step

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
                raise ValueError(
                    f"Chunk {chunk.index} has no blob_id; TTS may have failed"
                )

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

    # --- 3. Encode to MP3 ---
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

    log.info("stitch_final: saved final audio blob_id=%s", audio_blob.id)

    output = StitchStepOutput(
        final_audio_blob_id=str(audio_blob.id),
        final_video_blob_id=None,
        chunk_count=len(chunk_data),
        total_duration_sec=total_duration_sec,
    )

    # --- 5. Create video if images exist ---
    if image_output is not None and not isinstance(image_output, SkippedStepOutput):
        image_step_output = ImageStepOutput.model_validate(image_output)
        log.info(
            "stitch_final: creating video with %d scene images",
            len(image_step_output.scenes),
        )

        video_blob_id = await _create_video(
            scenes=image_step_output.scenes,
            mp3_bytes=mp3_bytes,
            workflow_id=workflow_id,
            session_maker=session_maker,
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
) -> uuid.UUID:
    """Create video from scene images and audio using ffmpeg.

    Args:
        scenes: Scene results from image_generation step (each has image_blob_id).
        mp3_bytes: Final MP3 audio bytes.
        workflow_id: Workflow UUID for blob storage.
        session_maker: SQLAlchemy async session factory.

    Returns:
        UUID of the saved video blob.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Fetch and write image files
        async with session_maker() as session:
            for i, scene in enumerate(scenes):
                blob_id_str = scene.image_blob_id
                if not blob_id_str:
                    continue
                blob = await blob_service.get(session, uuid.UUID(blob_id_str))
                img_path = tmpdir_path / f"image_{i:03d}.png"
                img_path.write_bytes(blob.data)

        # Write audio file
        mp3_path = tmpdir_path / "audio.mp3"
        mp3_path.write_bytes(mp3_bytes)

        # Create video with ffmpeg
        # Use 5 seconds per image, scale to 1280x720, match audio length
        video_path = tmpdir_path / "final_video.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-framerate", _FRAMERATE,
            "-pattern_type", "glob",
            "-i", str(tmpdir_path / "image_*.png"),
            "-i", str(mp3_path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={_VIDEO_SCALE}:force_original_aspect_ratio=decrease,pad={_VIDEO_SCALE}:(ow-iw)/2:(oh-ih)/2",
            "-c:a", "copy",
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
