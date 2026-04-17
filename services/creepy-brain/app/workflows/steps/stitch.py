"""stitch_final step executor.

Pulls WAV chunk blobs from Postgres, stitches locally, encodes to MP3,
optionally creates video with images using ffmpeg.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import structlog
from hatchet_sdk import Context
from pydantic import BaseModel, ConfigDict, Field

import app.db as _db  # module ref — always reads the live async_session_maker value
from app.audio.encoding import encode_wav_to_mp3
from app.models.enums import BlobType
from app.models.schemas import WorkflowInputSchema
from app.services import blob_service
from app.services.workflow_service import get_optional_workflow_id

log = structlog.get_logger(__name__)


class StitchStepOutput(BaseModel):
    """Output of the stitch_final step."""

    model_config = ConfigDict(extra="forbid")

    final_audio_blob_id: str = Field(description="UUID of the final MP3 blob")
    final_video_blob_id: str | None = Field(
        default=None, description="UUID of the final video blob (if created)"
    )
    chunk_count: int = Field(ge=0, description="Number of audio chunks stitched")
    total_duration_sec: float = Field(ge=0, description="Total audio duration in seconds")


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Stitch audio chunks and optionally create video with images.

    Args:
        input: Validated workflow input (contains stitch_video flag).
        ctx: Hatchet execution context (provides workflow_run_id and parent outputs).

    Returns:
        dict with keys: final_audio_blob_id, final_video_blob_id, chunk_count, total_duration_sec
        OR {"skipped": True, "reason": "..."} if stitch_video=False
    """
    # Early return if stitching disabled
    if not input.stitch_video:
        log.info("stitch_final skipped: stitch_video=False")
        return {"skipped": True, "reason": "stitch_video=False"}

    workflow_run_id: str = ctx.workflow_run_id
    workflow_id = get_optional_workflow_id(workflow_run_id)

    if workflow_id is None:
        raise ValueError(
            f"workflow_run_id={workflow_run_id} is not a valid UUID; "
            "stitch_final requires DB tracking"
        )

    log.info("stitch_final started workflow_id=%s", workflow_run_id)

    # Get parent step outputs
    parents: dict[str, Any] = ctx._data.parents
    image_output: dict[str, Any] = parents.get("image_generation", {})

    # --- 1. Fetch WAV chunk blobs from DB ---
    session_maker = _db.async_session_maker
    assert session_maker is not None, (
        "DB not initialized — call init_db() before starting the Hatchet worker"
    )

    async with session_maker() as session:
        from app.services.workflow_service import get_chunks_for_image_step

        chunk_data = await get_chunks_for_image_step(session, workflow_id)

    if not chunk_data:
        raise ValueError(
            f"No chunks found for workflow {workflow_run_id}; "
            "tts_synthesis step may not have completed"
        )

    log.info("stitch_final: %d chunks to stitch", len(chunk_data))

    # --- 2. Decode WAV blobs and concatenate ---
    arrays: list[np.ndarray[Any, np.dtype[np.float32]]] = []
    sample_rate: int | None = None

    async with session_maker() as session:
        for chunk in chunk_data:
            blob_id_str = chunk.get("blob_id")
            if not blob_id_str:
                raise ValueError(
                    f"Chunk {chunk.get('index')} has no blob_id; TTS may have failed"
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
                    chunk.get("index"),
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
    mp3_bytes = encode_wav_to_mp3(stitched, sample_rate)
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
    if not image_output.get("skipped") and image_output.get("scenes"):
        scenes = image_output["scenes"]
        log.info("stitch_final: creating video with %d scene images", len(scenes))

        video_blob_id = await _create_video(
            scenes=scenes,
            mp3_bytes=mp3_bytes,
            workflow_id=workflow_id,
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

    return output.model_dump()


async def _create_video(
    scenes: list[dict[str, Any]],
    mp3_bytes: bytes,
    workflow_id: uuid.UUID,
) -> uuid.UUID:
    """Create video from scene images and audio using ffmpeg.

    Args:
        scenes: Scene results from image_generation step (each has image_blob_id).
        mp3_bytes: Final MP3 audio bytes.
        workflow_id: Workflow UUID for blob storage.

    Returns:
        UUID of the saved video blob.
    """
    session_maker = _db.async_session_maker
    assert session_maker is not None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Fetch and write image files
        async with session_maker() as session:
            for i, scene in enumerate(scenes):
                blob_id_str = scene.get("image_blob_id")
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
            "-framerate", "1/5",  # 5 seconds per image
            "-pattern_type", "glob",
            "-i", str(tmpdir_path / "image_*.png"),
            "-i", str(mp3_path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
            "-c:a", "copy",
            "-shortest",
            str(video_path),
        ]

        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
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
