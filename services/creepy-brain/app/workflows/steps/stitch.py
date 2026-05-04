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

from app.engine import StepContext

from app.audio.encoding import encode_wav_to_mp3
from app.config import settings
from app.models.enums import BlobType, ChunkStatus
from app.models.json_schemas import MusicGenerationStepOutput, SfxGenerationStepOutput, WorkflowInputSchema
from app.models.workflow import WorkflowBlob
from app.services import blob_service
from app.services.workflow_service import (
    ChunkForImageStep,
    get_chunks_for_image_step,
    get_optional_workflow_id,
    get_scenes_for_workflow,
)
from app.text.captions import CaptionChunk, generate_srt
from app.text.scene_grouping import group_chunks_into_scenes
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
    subtitle_srt_blob_id: str | None = Field(
        default=None, description="UUID of the SRT subtitle blob (if created)"
    )
    chunk_count: int = Field(ge=0, description="Number of audio chunks stitched")
    total_duration_sec: float = Field(ge=0, description="Total audio duration in seconds")


async def execute(input: WorkflowInputSchema, ctx: StepContext) -> StitchStepOutput:
    """Stitch audio chunks and optionally create video with images.

    Supports resume: if final audio/video blobs already exist for this
    workflow, returns existing results without re-encoding.

    Args:
        input: Validated workflow input (contains stitch_video flag).
        ctx: step execution context (provides workflow_run_id and parent outputs).

    Returns:
        Pydantic output model, or skipped output if stitching is disabled.
    """
    workflow_run_id: str = ctx.workflow_run_id
    workflow_id = get_optional_workflow_id(workflow_run_id)

    if workflow_id is None:
        raise ValueError(
            f"workflow_run_id={workflow_run_id} is not a valid UUID; "
            "stitch_final requires DB tracking"
        )

    log.info("stitch_final started workflow_id=%s", workflow_run_id)

    session_maker = get_session_maker()

    # --- Resume check: look for existing final blobs (defer data to avoid loading MB) ---
    async with session_maker() as session:
        existing_blobs_result = await session.execute(
            select(
                WorkflowBlob.id,
                WorkflowBlob.blob_type,
            ).where(
                WorkflowBlob.workflow_id == workflow_id,
                WorkflowBlob.blob_type.in_([BlobType.FINAL_AUDIO, BlobType.FINAL_VIDEO, BlobType.SUBTITLE_SRT]),
            )
        )
        existing_blobs = {row.blob_type: row.id for row in existing_blobs_result.all()}

    existing_audio_id: uuid.UUID | None = existing_blobs.get(BlobType.FINAL_AUDIO)
    existing_video_id: uuid.UUID | None = existing_blobs.get(BlobType.FINAL_VIDEO)
    existing_srt_id: uuid.UUID | None = existing_blobs.get(BlobType.SUBTITLE_SRT)

    # If both exist (or audio exists and no video needed), return early
    needs_video = True  # Runner only calls this step when stitch_params.enabled=True
    if existing_audio_id is not None:
        if not needs_video or existing_video_id is not None:
            log.info(
                "stitch_final: resuming — final blobs already exist (audio=%s, video=%s, srt=%s)",
                existing_audio_id,
                existing_video_id,
                existing_srt_id,
            )
            async with session_maker() as session:
                chunk_data = await get_chunks_for_image_step(session, workflow_id)

            chunk_count = len([c for c in chunk_data if c.tts_status == ChunkStatus.COMPLETED])
            total_dur = sum(c.duration_sec or 0.0 for c in chunk_data if c.tts_status == ChunkStatus.COMPLETED)

            return StitchStepOutput(
                final_audio_blob_id=str(existing_audio_id),
                final_video_blob_id=str(existing_video_id) if existing_video_id else None,
                subtitle_srt_blob_id=str(existing_srt_id) if existing_srt_id else None,
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
    all_chunks = chunk_data  # full list (used for scene-timeline computation)
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
    has_partial_tts = len(chunk_data) < len(all_chunks)
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

    # --- 3. Mix music bed and SFX into narration ---
    # Skip mixing when some TTS chunks failed: scene timelines would be wrong
    # and SFX placement would be inaccurate.
    if has_partial_tts:
        log.warning(
            "stitch_final: skipping music/SFX mixing — %d/%d chunks have partial TTS",
            len(all_chunks) - len(chunk_data),
            len(all_chunks),
        )
        mixed = stitched
    else:
        mixed = await _mix_with_music_and_sfx(
            narration=stitched,
            sample_rate=sample_rate,
            chunk_data=chunk_data,
            ctx=ctx,
            session_maker=session_maker,
        )

    # --- 4. Encode to MP3 and store ---
    # Always re-encode from the mixed waveform. Reusing an existing FINAL_AUDIO blob
    # is unsafe because it may be narration-only (stored before music/SFX were added).
    mp3_bytes = await encode_wav_to_mp3(mixed, sample_rate)
    log.info("stitch_final: encoded MP3, size=%d bytes", len(mp3_bytes))

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

    # --- 4b. Generate and store SRT captions (skip if already stored from a prior attempt) ---
    caption_chunks = [
        CaptionChunk(
            text=chunk.normalized_text or chunk.text,
            duration_sec=chunk.duration_sec,
        )
        for chunk in chunk_data
        if chunk.duration_sec is not None and chunk.duration_sec > 0
    ]
    srt_blob_id: uuid.UUID | None = existing_srt_id
    if caption_chunks and existing_srt_id is None:
        srt_content = generate_srt(caption_chunks)
        log.info("stitch_final: generated SRT captions (%d bytes)", len(srt_content))
        async with session_maker() as session:
            srt_blob = await blob_service.store(
                session=session,
                data=srt_content.encode("utf-8"),
                mime_type="text/plain",
                blob_type=BlobType.SUBTITLE_SRT,
                workflow_id=workflow_id,
            )
            await session.commit()
            srt_blob_id = srt_blob.id
        log.info("stitch_final: saved SRT blob_id=%s", srt_blob_id)

    output = StitchStepOutput(
        final_audio_blob_id=str(audio_blob_id),
        final_video_blob_id=None,
        subtitle_srt_blob_id=str(srt_blob_id) if srt_blob_id is not None else None,
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



def _resample_to(
    arr: np.ndarray[Any, np.dtype[np.float32]],
    src_sr: int,
    dst_sr: int,
) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Linearly resample *arr* from *src_sr* to *dst_sr*.

    Uses numpy linear interpolation — no extra deps required.

    Args:
        arr: Input float32 array (1-D mono or 2-D [samples, channels]).
        src_sr: Source sample rate in Hz.
        dst_sr: Target sample rate in Hz.

    Returns:
        Resampled float32 array with the same number of channels.
    """
    if src_sr == dst_sr:
        return arr
    duration = len(arr) / src_sr
    n_dst = max(1, int(round(duration * dst_sr)))
    x_src = np.linspace(0.0, duration, len(arr), endpoint=False)
    x_dst = np.linspace(0.0, duration, n_dst, endpoint=False)
    resampled: np.ndarray[Any, np.dtype[np.float32]]
    if arr.ndim == 1:
        resampled = np.interp(x_dst, x_src, arr).astype(np.float32)
    else:
        channels = arr.shape[1]
        resampled = np.stack(
            [np.interp(x_dst, x_src, arr[:, ch]).astype(np.float32) for ch in range(channels)],
            axis=1,
        )
    return resampled


async def _mix_with_music_and_sfx(
    narration: np.ndarray[Any, np.dtype[np.float32]],
    sample_rate: int,
    chunk_data: list[ChunkForImageStep],
    ctx: StepContext,
    session_maker: async_sessionmaker[AsyncSession],
) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Mix music bed and SFX clips into the narration array.

    Does nothing (returns narration unchanged) if both music and SFX steps
    are skipped or absent. Music is ducked to ``settings.music_volume_db`` dB
    and looped to match narration length. SFX clips are placed at scene-relative
    offsets determined by their ``position`` field.

    All layers are resampled to the narration sample rate before mixing to
    prevent pitch/speed artefacts.

    Args:
        narration: Concatenated narration float32 array (mono or stereo).
        sample_rate: Sample rate of the narration array.
        chunk_data: Completed chunks (used to compute per-scene start times).
        ctx: Step context with parent outputs.
        session_maker: DB session factory.

    Returns:
        Mixed float32 array, clipped to [-1, 1].
    """
    music_out = ctx.get_parent_output("music_generation", MusicGenerationStepOutput)
    sfx_out = ctx.get_parent_output("sfx_generation", SfxGenerationStepOutput)

    if music_out is None and (sfx_out is None or not sfx_out.clips):
        return narration

    result: np.ndarray[Any, np.dtype[np.float32]] = narration.copy()
    n_samples = len(result)

    # --- Music bed ---
    if music_out is not None:
        async with session_maker() as session:
            music_blob = await blob_service.get(session, uuid.UUID(music_out.music_bed_blob_id))
        music_arr: np.ndarray[Any, np.dtype[np.float32]]
        music_arr, music_sr = sf.read(io.BytesIO(bytes(music_blob.data)), dtype="float32")

        if music_sr != sample_rate:
            log.info(
                "stitch_final: resampling music bed %dHz -> %dHz",
                music_sr,
                sample_rate,
            )
            music_arr = _resample_to(music_arr, music_sr, sample_rate)

        # Downmix stereo music to mono if narration is mono, or broadcast the other way
        if music_arr.ndim > 1 and result.ndim == 1:
            music_arr = music_arr.mean(axis=1)
        elif music_arr.ndim == 1 and result.ndim > 1:
            music_arr = np.stack([music_arr] * result.shape[1], axis=-1)

        # Loop music to match narration length
        while len(music_arr) < n_samples:
            music_arr = np.concatenate([music_arr, music_arr])
        music_arr = music_arr[:n_samples]

        gain = 10.0 ** (settings.music_volume_db / 20.0)
        result = result + (music_arr * gain).astype(np.float32)
        log.info(
            "stitch_final: mixed music bed blob=%s at %.1f dB",
            music_out.music_bed_blob_id,
            settings.music_volume_db,
        )

    # --- SFX clips ---
    if sfx_out is not None and sfx_out.clips:
        # Compute per-scene start times from sorted chunk_data.
        # Chunks are sorted by index; group_chunks_into_scenes assigns 0-based
        # positional indices, so scene.chunk_indices[j] is the j-th chunk in
        # sorted order (not the chunk's .index field).
        sorted_chunks = sorted(chunk_data, key=lambda c: c.index)
        scenes = group_chunks_into_scenes(
            chunks=[c.text for c in sorted_chunks],
            chunks_per_scene=settings.chunks_per_scene,
        )

        # Accumulate per-chunk start times (positional, matching sorted_chunks)
        chunk_start_sec: list[float] = []
        t = 0.0
        for c in sorted_chunks:
            chunk_start_sec.append(t)
            t += c.duration_sec or 0.0

        scene_start_by_idx: dict[int, float] = {}
        scene_dur_by_idx: dict[int, float] = {}
        for scene in scenes:
            if scene.chunk_indices:
                first = scene.chunk_indices[0]
                scene_start_by_idx[scene.scene_index] = (
                    chunk_start_sec[first] if first < len(chunk_start_sec) else 0.0
                )
                scene_dur_by_idx[scene.scene_index] = sum(
                    sorted_chunks[ci].duration_sec or 0.0
                    for ci in scene.chunk_indices
                    if ci < len(sorted_chunks)
                )

        sfx_gain = 10.0 ** (settings.sfx_volume_db / 20.0)
        async with session_maker() as session:
            for clip in sfx_out.clips:
                if clip.scene_index not in scene_start_by_idx:
                    log.warning(
                        "stitch_final: SFX clip scene=%d not in scene map, skipping",
                        clip.scene_index,
                    )
                    continue

                scene_start = scene_start_by_idx[clip.scene_index]
                scene_dur = scene_dur_by_idx[clip.scene_index]

                if clip.position == "beginning":
                    offset_sec = scene_start
                elif clip.position == "middle":
                    offset_sec = scene_start + max(
                        0.0, scene_dur / 2.0 - clip.duration_sec / 2.0
                    )
                else:  # "end"
                    offset_sec = scene_start + max(
                        0.0, scene_dur - clip.duration_sec
                    )

                sfx_blob = await blob_service.get(session, uuid.UUID(clip.blob_id))
                sfx_arr: np.ndarray[Any, np.dtype[np.float32]]
                sfx_arr, sfx_sr = sf.read(
                    io.BytesIO(bytes(sfx_blob.data)), dtype="float32"
                )
                if sfx_sr != sample_rate:
                    log.info(
                        "stitch_final: resampling SFX clip scene=%d cue=%d %dHz -> %dHz",
                        clip.scene_index,
                        clip.cue_index,
                        sfx_sr,
                        sample_rate,
                    )
                    sfx_arr = _resample_to(sfx_arr, sfx_sr, sample_rate)

                if sfx_arr.ndim > 1 and result.ndim == 1:
                    sfx_arr = sfx_arr.mean(axis=1)
                elif sfx_arr.ndim == 1 and result.ndim > 1:
                    sfx_arr = np.stack([sfx_arr] * result.shape[1], axis=-1)

                sfx_start = int(offset_sec * sample_rate)
                sfx_end = min(sfx_start + len(sfx_arr), n_samples)
                sfx_len = sfx_end - sfx_start
                if sfx_len > 0:
                    result[sfx_start:sfx_end] += (sfx_arr[:sfx_len] * sfx_gain).astype(
                        np.float32
                    )
                    log.info(
                        "stitch_final: mixed SFX clip scene=%d cue=%d pos=%s offset=%.1fs",
                        clip.scene_index,
                        clip.cue_index,
                        clip.position,
                        offset_sec,
                    )

    return np.clip(result, -1.0, 1.0, out=result)


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
    session_maker = get_session_maker()

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
