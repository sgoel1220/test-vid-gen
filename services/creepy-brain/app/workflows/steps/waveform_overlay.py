"""waveform_overlay step executor.

Composites a semi-transparent waveform line overlay onto the stitched video.

The waveform reacts to narrator audio amplitude near the playhead: a smooth
upper/lower line pair tracks the coarse amplitude envelope, with a local boost
around the current playhead position. Glow and line are alpha-composited
directly onto the original video frames using rawvideo I/O for efficiency.
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
_SAMPLE_RATE = 22050              # audio decoding sample rate (Hz)
_N_BANDS = 32                       # number of FFT frequency bands
_BAND_SMOOTHING = 0.4               # temporal smoothing factor (exponential MA)


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


def _compute_fft_bands(
    samples: np.ndarray[Any, np.dtype[np.float32]],
    fps: float,
    n_bands: int = _N_BANDS,
) -> list[list[float]]:
    """Compute per-frame energy in n_bands frequency bands via numpy FFT.

    Each video frame's audio window is transformed to the frequency domain.
    The positive-frequency spectrum is split into n_bands equal-width bins
    and mean energy per bin is computed.  Temporal smoothing via exponential
    moving average is applied across frames to produce natural bar movement.

    Returns:
        List of length total_frames, each entry is a list of n_bands values
        in [0, 1] representing globally-normalized energy per frequency band.
    """
    samples_per_frame = max(1, int(_SAMPLE_RATE / fps))
    total_frames = (len(samples) + samples_per_frame - 1) // samples_per_frame

    all_bands: list[list[float]] = []
    prev_bands: list[float] = [0.0] * n_bands

    for i in range(total_frames):
        start = i * samples_per_frame
        seg = samples[start : start + samples_per_frame]
        if len(seg) == 0:
            all_bands.append(list(prev_bands))
            continue

        # Nearest power-of-two pad for FFT efficiency
        fft_size = 1
        while fft_size < len(seg):
            fft_size <<= 1
        padded = np.zeros(fft_size, dtype=np.float32)
        padded[: len(seg)] = seg

        # Hann window to reduce spectral leakage
        padded[: len(seg)] *= np.hanning(len(seg)).astype(np.float32)

        # Positive-frequency magnitude spectrum
        spectrum = np.abs(np.fft.rfft(padded))
        n_bins = len(spectrum)

        # Equal-width frequency bands
        raw_bands: list[float] = []
        for b in range(n_bands):
            lo = int(b * n_bins / n_bands)
            hi = max(int((b + 1) * n_bins / n_bands), lo + 1)
            raw_bands.append(float(np.mean(spectrum[lo:hi])))

        # Exponential moving average for smooth bar animation
        smoothed = [
            _BAND_SMOOTHING * p + (1.0 - _BAND_SMOOTHING) * c
            for p, c in zip(prev_bands, raw_bands)
        ]
        prev_bands = smoothed
        all_bands.append(smoothed)

    # Global normalization so the loudest band/frame == 1.0
    flat = [v for frame in all_bands for v in frame]
    global_max = max(flat) if flat else 1.0
    if global_max < 1e-9:
        global_max = 1.0
    return [[v / global_max for v in frame] for frame in all_bands]


def _render_overlay_frame(
    video_frame: np.ndarray[Any, np.dtype[np.uint8]],
    band_energies: list[list[float]],
    clip_env: list[float],
    frame_idx: int,
    total_frames: int,
    vid_w: int,
    vid_h: int,
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Composite a semi-transparent waveform line onto a video frame.

    Uses a clip-wide time-domain amplitude envelope (``clip_env``) for the
    x-axis so the waveform shape represents actual audio loudness across the
    whole narration.  FFT band energies drive the playhead-local boost.

    NOTE: This renderer will be replaced by a vertical equalizer bar renderer
    in the next step (bead arv).  The band_energies / clip_env parameters
    are the new interface; the waveform logic here is temporary bridging code.
    """
    current_bands = band_energies[min(frame_idx, len(band_energies) - 1)]
    current_amp = float(np.mean(current_bands))

    center_y = vid_h // 2
    playhead_x = int(frame_idx / max(total_frames - 1, 1) * vid_w)
    max_disp = vid_h * _WAVE_AMP_MAX
    n_env = len(clip_env)

    # Build y-values for upper and lower waveform edges
    upper_pts: list[tuple[int, int]] = []
    lower_pts: list[tuple[int, int]] = []
    for x in range(vid_w):
        # Map x position to clip time for the time-domain amplitude envelope
        env_idx = min(int(x / vid_w * n_env), n_env - 1)
        env_val = clip_env[env_idx]

        # Playhead proximity boost — monotonic: amp never drops below env_val
        dx = abs(x - playhead_x)
        if dx < _PLAYHEAD_FALLOFF_PX:
            t = 1.0 - dx / _PLAYHEAD_FALLOFF_PX
            boost_target = max(env_val, current_amp * _PLAYHEAD_BOOST)
            amp = env_val + t * (boost_target - env_val)
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
    """Composite a semi-transparent waveform line overlay onto the stitched video.

    Produces a WAVEFORM_VIDEO blob: the original video frames with a
    waveform line composited on top. The waveform reacts to narrator audio
    amplitude near the playhead position.

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

        # Decode audio and compute per-frame FFT frequency bands
        samples = await _decode_audio_f32(str(audio_in))
        band_energies = _compute_fft_bands(samples, fps)
        # Clip-wide time-domain amplitude envelope (mean of all bands per frame)
        # used by _render_overlay_frame for the x-axis shape.  Pre-computed
        # once here to avoid O(n_frames²) work inside the per-frame loop.
        clip_env: list[float] = [float(np.mean(bands)) for bands in band_energies]

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
                video_frame, band_energies, clip_env,
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
