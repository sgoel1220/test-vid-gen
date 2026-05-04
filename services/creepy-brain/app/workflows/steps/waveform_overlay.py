"""waveform_overlay step executor.

Generates a standalone animated waveform visualization video.

Dark navy background, full-width symmetric bars above/below a center
baseline. A cross/diamond burst at the playhead pulses per word based on
per-frame audio amplitude. Quiet bars render as tiny nubs (dotted rope).
Staircase quantization gives a pixelated/quantized aesthetic.
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
from PIL import Image, ImageDraw
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
_LINE_COLOR = (255, 255, 255)       # white line
_LINE_ALPHA = 0.6                   # line opacity
_GLOW_COLOR = (180, 200, 255)       # subtle blue-white glow
_GLOW_ALPHA = 0.25
_GLOW_RADIUS = 3
_LINE_WIDTH = 2
_WAVE_AMP_MAX = 0.3                 # max displacement as fraction of frame height
_PLAYHEAD_BOOST = 1.8               # amplitude multiplier near playhead
_PLAYHEAD_FALLOFF_PX = 200          # fade distance for playhead boost
_COARSE_STEPS = 400               # envelope resolution for background shape
_SAMPLE_RATE = 22050              # audio decoding sample rate (Hz)


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
    """Compute per-frame RMS amplitude normalized 0→1.

    One value per video frame — gives per-word/syllable reactivity since
    amplitude naturally rises during stressed syllables and drops in pauses.
    """
    samples_per_frame = max(1, int(_SAMPLE_RATE / fps))
    total_frames = (len(samples) + samples_per_frame - 1) // samples_per_frame
    amps: list[float] = []
    for i in range(total_frames):
        start = i * samples_per_frame
        seg = samples[start : start + samples_per_frame]
        rms = float(np.sqrt(np.mean(seg**2))) if len(seg) > 0 else 0.0
        amps.append(rms)
    max_val = max(amps) if any(amps) else 1.0
    if max_val < 1e-9:
        max_val = 1.0
    return [v / max_val for v in amps]


def _render_overlay_frame(
    video_frame: np.ndarray[Any, np.dtype[np.uint8]],
    coarse_envs: list[float],
    frame_amps: list[float],
    frame_idx: int,
    total_frames: int,
    vid_w: int,
    vid_h: int,
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Composite a semi-transparent waveform line onto a video frame."""
    center_y = vid_h // 2
    playhead_x = int(frame_idx / max(total_frames - 1, 1) * vid_w)
    current_amp = frame_amps[min(frame_idx, len(frame_amps) - 1)]
    max_disp = vid_h * _WAVE_AMP_MAX

    # Build y-values for upper and lower waveform edges
    upper_pts: list[tuple[int, int]] = []
    lower_pts: list[tuple[int, int]] = []
    for x in range(vid_w):
        # Envelope height from coarse envelope
        env_idx = int(x / vid_w * len(coarse_envs))
        env_idx = min(env_idx, len(coarse_envs) - 1)
        env_val = coarse_envs[env_idx]

        # Playhead proximity boost
        dx = abs(x - playhead_x)
        if dx < _PLAYHEAD_FALLOFF_PX:
            t = 1.0 - dx / _PLAYHEAD_FALLOFF_PX
            amp = env_val + t * (current_amp * _PLAYHEAD_BOOST - env_val)
        else:
            amp = env_val

        disp = int(amp * max_disp)
        upper_pts.append((x, center_y - disp))
        lower_pts.append((x, center_y + disp))

    # Create RGBA overlay for alpha compositing
    base = Image.fromarray(video_frame, mode="RGB").convert("RGBA")
    overlay = Image.new("RGBA", (vid_w, vid_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw glow (wider, lower alpha)
    glow_alpha = int(_GLOW_ALPHA * 255)
    glow_color = (*_GLOW_COLOR, glow_alpha)
    if len(upper_pts) >= 2:
        draw.line(upper_pts, fill=glow_color, width=_LINE_WIDTH + _GLOW_RADIUS * 2)
        draw.line(lower_pts, fill=glow_color, width=_LINE_WIDTH + _GLOW_RADIUS * 2)

    # Draw main line
    line_alpha = int(_LINE_ALPHA * 255)
    line_color = (*_LINE_COLOR, line_alpha)
    if len(upper_pts) >= 2:
        draw.line(upper_pts, fill=line_color, width=_LINE_WIDTH)
        draw.line(lower_pts, fill=line_color, width=_LINE_WIDTH)

    # Composite and convert back to RGB numpy array
    composited = Image.alpha_composite(base, overlay)
    return np.array(composited.convert("RGB"), dtype=np.uint8)


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> WaveformOverlayStepOutput | SkippedStepOutput:
    """Generate standalone animated waveform visualization video.

    Produces a WAVEFORM_VIDEO blob: dark navy full-frame background with
    symmetric waveform bars and a cross/diamond burst at the playhead that
    pulses per word based on audio amplitude.

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

        # Decode audio and compute envelopes
        samples = await _decode_audio_f32(str(audio_in))
        coarse_envs = _compute_envelope(samples, _COARSE_STEPS)
        frame_amps = _compute_frame_amplitudes(samples, fps)

        duration_sec = stitch_out.total_duration_sec
        total_frames = max(1, int(duration_sec * fps))

        log.info("waveform_overlay: rendering %d frames", total_frames)

        # -- video reader: decode input video to raw RGB24 frames --
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
        frame_bytes = vid_w * vid_h * 3

        ffmpeg_cmd = [
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
            *ffmpeg_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert reader_proc.stdout is not None
        assert writer_proc.stdin is not None

        for frame_idx in range(total_frames):
            raw = await reader_proc.stdout.readexactly(frame_bytes)
            video_frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (vid_h, vid_w, 3)
            )
            overlay = _render_overlay_frame(
                video_frame, coarse_envs, frame_amps,
                frame_idx, total_frames, vid_w, vid_h,
            )
            writer_proc.stdin.write(overlay.tobytes())

        writer_proc.stdin.close()
        _, stderr_bytes = await writer_proc.communicate()

        # Clean up reader
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
