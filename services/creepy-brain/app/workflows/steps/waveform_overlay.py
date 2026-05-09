"""waveform_overlay step executor.

Composites an animated waveform onto the stitched video.

Static symmetric dark-grey bars represent the full audio timeline across the
bottom strip of the frame. A playhead moves left-to-right as the audio plays.
Bars are drawn directly onto the video frames.
"""

from __future__ import annotations

import asyncio
import struct
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from sqlalchemy import select

from app.engine import SkippedStepOutput, StepContext
from app.models.enums import BlobType
from app.models.json_schemas import WaveformOverlayStepOutput, WorkflowInputSchema
from app.models.workflow import WorkflowBlob
from app.services import blob_service
from app.services.workflow_service import get_optional_workflow_id
from app.workflows.db_helpers import get_session_maker
from app.workflows.steps.stitch import StitchStepOutput

log = structlog.get_logger(__name__)

# --- Visual constants ---
_BAR_COLOR = (80, 80, 80)          # Dark grey bars
_BAR_W = 2                         # Bar width in pixels
_BAR_GAP = 2                       # Gap between bars (stride = 4px)
_BAR_CENTER_Y_FRAC = 0.88          # Center line of bar strip (bottom 25%)
_BAR_ZONE_HALF_FRAC = 0.10         # Half-height of bar zone as fraction of frame
_COARSE_STEPS = 10000              # Envelope resolution
_SAMPLE_RATE = 22050               # Audio decoding sample rate (Hz)


async def _ffprobe_video(path: str) -> tuple[float, int, int]:
    """Return (fps, width, height) via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "csv=p=0",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()}")
    parts = stdout.decode().strip().split(",")
    width = int(parts[0])
    height = int(parts[1])
    num, denom = parts[2].split("/")
    fps = float(num) / float(denom)
    return fps, width, height


async def _decode_audio_f32(audio_path: str) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Decode audio to mono f32 PCM at _SAMPLE_RATE Hz via ffmpeg pipe."""
    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-f", "f32le",
        "-ar", str(_SAMPLE_RATE),
        "-ac", "1",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg audio decode failed")
    n_samples = len(stdout) // 4
    samples = struct.unpack(f"<{n_samples}f", stdout[: n_samples * 4])
    return np.array(samples, dtype=np.float32)


def _compute_envelope(
    samples: np.ndarray[Any, np.dtype[np.float32]], n_steps: int
) -> list[float]:
    """Compute RMS envelope across n_steps segments, normalized 0→1."""
    seg_len = max(1, len(samples) // n_steps)
    envs: list[float] = []
    for i in range(n_steps):
        seg = samples[i * seg_len : (i + 1) * seg_len]
        rms = float(np.sqrt(np.mean(seg**2))) if len(seg) > 0 else 0.0
        envs.append(rms)
    max_val = max(envs) if any(envs) else 1.0
    if max_val < 1e-9:
        max_val = 1.0
    return [v / max_val for v in envs]


def _compute_frame_amplitudes(
    samples: np.ndarray[Any, np.dtype[np.float32]], fps: float
) -> list[float]:
    """Compute per-frame RMS amplitude normalized 0→1."""
    samples_per_frame = max(1, int(_SAMPLE_RATE / fps))
    total_frames = (len(samples) + samples_per_frame - 1) // samples_per_frame
    amps: list[float] = []
    for i in range(total_frames):
        seg = samples[i * samples_per_frame : (i + 1) * samples_per_frame]
        rms = float(np.sqrt(np.mean(seg**2))) if len(seg) > 0 else 0.0
        amps.append(rms)
    max_val = max(amps) if any(amps) else 1.0
    if max_val < 1e-9:
        max_val = 1.0
    return [v / max_val for v in amps]


def _render_frame(
    video_frame: np.ndarray[Any, np.dtype[np.uint8]],
    coarse_envs: list[float],
    frame_amps: list[float],
    frame_idx: int,
    total_frames: int,
    vid_w: int,
    vid_h: int,
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Composite waveform bars onto a video frame.

    Bar heights = envelope shape × current frame amplitude, so bars animate
    up/down with the audio. The playhead moves left-to-right. Bars confined
    to a bottom strip centered at _BAR_CENTER_Y_FRAC.
    """
    frame: np.ndarray[Any, np.dtype[np.uint8]] = video_frame.copy()

    center_y = int(vid_h * _BAR_CENTER_Y_FRAC)
    zone_half_h = int(vid_h * _BAR_ZONE_HALF_FRAC)

    current_amp = frame_amps[min(frame_idx, len(frame_amps) - 1)]
    n_envs = len(coarse_envs)
    stride = _BAR_W + _BAR_GAP

    x = 0
    while x + _BAR_W <= vid_w:
        audio_frac = x / vid_w
        env_idx = min(n_envs - 1, int(audio_frac * n_envs))
        # Animate: envelope shape × current amplitude
        bar_h = max(1, int(coarse_envs[env_idx] * current_amp * zone_half_h))

        y0 = max(0, center_y - bar_h)
        y1 = min(vid_h, center_y + bar_h)
        frame[y0:y1, x : x + _BAR_W] = _BAR_COLOR

        x += stride

    return frame


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> WaveformOverlayStepOutput | SkippedStepOutput:
    """Generate standalone animated waveform visualization video.

    Produces a WAVEFORM_VIDEO blob: near-black full-frame background with
    scrolling symmetric white bars in the lower half, amplitude-driven from
    a high-resolution audio envelope.

    Args:
        input: Workflow input schema.
        ctx: Step context with parent outputs.

    Returns:
        WaveformOverlayStepOutput on success, SkippedStepOutput if no video.
    """
    workflow_run_id: str = ctx.workflow_run_id
    workflow_id = get_optional_workflow_id(workflow_run_id)

    if workflow_id is None:
        raise ValueError(f"workflow_run_id={workflow_run_id} is not a valid UUID")

    session_maker = get_session_maker()

    # --- Resume check: waveform blob already stored? ---
    async with session_maker() as session:
        existing = await session.execute(
            select(WorkflowBlob.id).where(
                WorkflowBlob.workflow_id == workflow_id,
                WorkflowBlob.blob_type == BlobType.WAVEFORM_VIDEO,
            )
        )
        existing_row = existing.first()

    if existing_row is not None:
        existing_id: uuid.UUID = existing_row[0]
        log.info("waveform_overlay: resuming — blob already exists id=%s", existing_id)
        async with session_maker() as session:
            blob = await blob_service.get(session, existing_id)
        return WaveformOverlayStepOutput(
            waveform_video_blob_id=existing_id,
            file_size_bytes=len(blob.data),
        )

    # --- Get stitch_final output ---
    stitch_out = ctx.get_parent_output("stitch_final", StitchStepOutput)
    if stitch_out is None or isinstance(stitch_out, SkippedStepOutput):
        log.info("waveform_overlay: skipping — stitch_final was skipped or missing")
        return SkippedStepOutput(reason="stitch_final skipped or missing")

    if stitch_out.final_video_blob_id is None:
        log.info("waveform_overlay: skipping — stitch_final produced no video")
        return SkippedStepOutput(reason="stitch_final produced no video")

    video_blob_id = uuid.UUID(stitch_out.final_video_blob_id)
    audio_blob_id = uuid.UUID(stitch_out.final_audio_blob_id)

    log.info("waveform_overlay: starting workflow_id=%s", workflow_run_id)

    # --- Fetch blobs ---
    async with session_maker() as session:
        video_blob = await blob_service.get(session, video_blob_id)
        audio_blob = await blob_service.get(session, audio_blob_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        video_in = tmpdir_path / "input_video.mp4"
        audio_in = tmpdir_path / "audio.mp3"
        video_out = tmpdir_path / "waveform_video.mp4"

        video_in.write_bytes(video_blob.data)
        audio_in.write_bytes(audio_blob.data)

        # Probe input video for dimensions and FPS
        fps, vid_w, vid_h = await _ffprobe_video(str(video_in))
        log.info("waveform_overlay: video %dx%d @%.2ffps", vid_w, vid_h, fps)

        # Decode audio and compute envelope + per-frame amplitudes
        samples = await _decode_audio_f32(str(audio_in))
        coarse_envs = _compute_envelope(samples, _COARSE_STEPS)
        frame_amps = _compute_frame_amplitudes(samples, fps)

        duration_sec = stitch_out.total_duration_sec
        total_frames = max(1, int(duration_sec * fps))
        frame_bytes = vid_w * vid_h * 3

        log.info("waveform_overlay: rendering %d frames", total_frames)

        # Reader: decode input video to raw RGB24 frames
        reader_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_in),
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-v", "error",
            "pipe:1",
        ]
        reader_proc = await asyncio.create_subprocess_exec(
            *reader_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Writer: raw RGB24 frames + audio → output video
        writer_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{vid_w}x{vid_h}",
            "-r", f"{fps:.6f}",
            "-i", "pipe:0",
            "-i", str(audio_in),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(video_out),
        ]
        writer_proc = await asyncio.create_subprocess_exec(
            *writer_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert reader_proc.stdout is not None
        assert writer_proc.stdin is not None

        for frame_idx in range(total_frames):
            raw = await reader_proc.stdout.readexactly(frame_bytes)
            video_frame = np.frombuffer(raw, dtype=np.uint8).reshape((vid_h, vid_w, 3))
            composited = _render_frame(
                video_frame, coarse_envs, frame_amps,
                frame_idx, total_frames, vid_w, vid_h,
            )
            writer_proc.stdin.write(composited.tobytes())

        writer_proc.stdin.close()
        _, stderr_bytes = await writer_proc.communicate()
        await reader_proc.communicate()

        if writer_proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg waveform_overlay failed: {stderr}")

        video_bytes = video_out.read_bytes()
        log.info("waveform_overlay: encoded %d bytes", len(video_bytes))

    # --- Store waveform video blob ---
    async with session_maker() as session:
        wf_blob = await blob_service.store(
            session=session,
            data=video_bytes,
            mime_type="video/mp4",
            blob_type=BlobType.WAVEFORM_VIDEO,
            workflow_id=workflow_id,
        )
        await session.commit()

    log.info("waveform_overlay: saved blob_id=%s", wf_blob.id)

    return WaveformOverlayStepOutput(
        waveform_video_blob_id=wf_blob.id,
        file_size_bytes=len(video_bytes),
    )
