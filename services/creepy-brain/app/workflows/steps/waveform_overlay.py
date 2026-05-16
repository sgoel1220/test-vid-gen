"""waveform_overlay step executor.

Composites animated vertical equalizer bars onto the stitched video.

Each bar represents a frequency band derived from the narrator audio via FFT
analysis.  Bar heights react to per-frame band energy with temporal smoothing.
A base-to-tip color gradient and a glow halo are alpha-composited directly
onto the original video frames using rawvideo I/O for efficiency.
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
from app.models.json_schemas import StitchFinalStepOutput, WaveformOverlayStepOutput, WorkflowInputSchema
from app.models.workflow import WorkflowBlob
from app.services import blob_service
from app.services.workflow_service import get_optional_workflow_id
from app.workflows.db_helpers import get_session_maker

log = structlog.get_logger(__name__)

# --- Audio analysis constants ---
_SAMPLE_RATE = 22050                # audio decoding sample rate (Hz)
_N_BANDS = 32                       # number of FFT frequency bands
_BAND_SMOOTHING = 0.4               # temporal smoothing factor (exponential MA)

# --- Bar renderer constants ---
_BAR_COUNT = 32                     # number of equalizer bars (matched to _N_BANDS)
_BAR_WIDTH = 12                     # pixels per bar
_BAR_GAP = 4                        # pixels between bars
_BAR_MAX_HEIGHT = 0.22              # max bar height as fraction of frame height
_BAR_BOTTOM_PAD = 28                # pixels from bottom of frame to bar base
_BAR_BASE_COLOR = (0, 0, 0)         # gradient base color (center of bar)
_BAR_TIP_COLOR = (30, 30, 30)       # gradient tip color (edges of bar)
_BAR_ALPHA = 1.0                    # bar opacity
_BAR_GLOW_WIDTH = 5                 # pixels of glow expansion beyond bar edges
_BAR_GLOW_ALPHA = 0.0               # glow opacity (disabled for black bars)


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
    frame_idx: int,
    vid_w: int,
    vid_h: int,
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Composite vertical equalizer bars onto a video frame.

    Bars are centered horizontally at the bottom of the frame.  Each bar's
    height is proportional to the corresponding FFT frequency band energy for
    the current frame.  A base-to-tip color gradient and a glow halo are
    alpha-composited on top of the video frame.

    Args:
        video_frame: RGB uint8 array of shape (vid_h, vid_w, 3).
        band_energies: Per-frame list of per-band energies in [0, 1].
        frame_idx: Index of the current frame.
        vid_w: Frame width in pixels.
        vid_h: Frame height in pixels.

    Returns:
        RGB uint8 array with bars composited onto the video frame.
    """
    current_bands = band_energies[min(frame_idx, len(band_energies) - 1)]
    n_bars = min(_BAR_COUNT, len(current_bands))

    total_width = n_bars * _BAR_WIDTH + max(0, n_bars - 1) * _BAR_GAP
    x_start = (vid_w - total_width) // 2
    bar_max_h = int(vid_h * _BAR_MAX_HEIGHT)
    y_bottom = int(vid_h * 0.75)

    # --- Glow pass (PIL draw — wider translucent halo behind each bar) ---
    glow_overlay = Image.new("RGBA", (vid_w, vid_h), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_overlay)
    glow_rgba = (*_BAR_TIP_COLOR, int(_BAR_GLOW_ALPHA * 255))

    # --- Main bar pass (numpy gradient fill for performance) ---
    bar_arr = np.zeros((vid_h, vid_w, 4), dtype=np.uint8)

    def _gradient(t: np.ndarray[Any, np.dtype[np.float32]]) -> np.ndarray[Any, np.dtype[np.uint8]]:
        r = np.clip(_BAR_BASE_COLOR[0] + t * (_BAR_TIP_COLOR[0] - _BAR_BASE_COLOR[0]), 0, 255).astype(np.uint8)
        g = np.clip(_BAR_BASE_COLOR[1] + t * (_BAR_TIP_COLOR[1] - _BAR_BASE_COLOR[1]), 0, 255).astype(np.uint8)
        b = np.clip(_BAR_BASE_COLOR[2] + t * (_BAR_TIP_COLOR[2] - _BAR_BASE_COLOR[2]), 0, 255).astype(np.uint8)
        a = np.full(len(t), int(_BAR_ALPHA * 255), dtype=np.uint8)
        return np.stack([r, g, b, a], axis=1)

    for i in range(n_bars):
        energy = current_bands[i]
        bar_h = max(2, int(energy * bar_max_h))

        x_lo = x_start + i * (_BAR_WIDTH + _BAR_GAP)
        x_hi = min(vid_w, x_lo + _BAR_WIDTH)
        y_top = max(0, y_bottom - bar_h)

        if x_lo >= vid_w or x_hi <= x_lo:
            continue
        h = y_bottom - y_top
        if h <= 0:
            continue

        # Glow: expanded rectangle, lower alpha, tip color
        gx_lo = max(0, x_lo - _BAR_GLOW_WIDTH)
        gx_hi = min(vid_w, x_hi + _BAR_GLOW_WIDTH)
        gy_top = max(0, y_top - _BAR_GLOW_WIDTH)
        glow_draw.rectangle([gx_lo, gy_top, gx_hi - 1, y_bottom - 1], fill=glow_rgba)

        # --- Upward bar: gradient t=0 at bottom (base), t=1 at top (tip) ---
        t_up = np.linspace(0.0, 1.0, h, dtype=np.float32)[::-1]  # shape (h,)

        bar_w = x_hi - x_lo
        up_colors = _gradient(t_up)
        bar_arr[y_top:y_bottom, x_lo:x_hi] = np.broadcast_to(
            up_colors[:, np.newaxis, :], (h, bar_w, 4)
        )

        # --- Downward bar: mirror below y_bottom ---
        y_bot_down = min(vid_h, y_bottom + bar_h)
        h_down = y_bot_down - y_bottom
        if h_down > 0:
            # t=0 at top (center/base color), t=1 at bottom (tip color)
            t_down = np.linspace(0.0, 1.0, h_down, dtype=np.float32)
            down_colors = _gradient(t_down)
            bar_arr[y_bottom:y_bot_down, x_lo:x_hi] = np.broadcast_to(
                down_colors[:, np.newaxis, :], (h_down, bar_w, 4)
            )
            # Glow below
            gy_bot_down = min(vid_h, y_bot_down + _BAR_GLOW_WIDTH)
            glow_draw.rectangle([gx_lo, y_bottom, gx_hi - 1, gy_bot_down - 1], fill=glow_rgba)

    # Composite: video base → glow → bars
    base = Image.fromarray(video_frame, mode="RGB").convert("RGBA")
    bar_img = Image.fromarray(bar_arr, mode="RGBA")
    composited = Image.alpha_composite(base, glow_overlay)
    composited = Image.alpha_composite(composited, bar_img)
    return np.array(composited.convert("RGB"), dtype=np.uint8)


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> WaveformOverlayStepOutput | SkippedStepOutput:
    """Composite animated vertical equalizer bars onto the stitched video.

    Produces a WAVEFORM_VIDEO blob: the original video frames with
    equalizer bars composited at the bottom-center.  Bar heights react to
    narrator audio frequency band energy derived via per-frame FFT analysis.

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
    stitch_out = ctx.get_parent_output("stitch_final", StitchFinalStepOutput)
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

        frame_idx = 0
        buf = bytearray()
        while True:
            chunk = await reader_proc.stdout.read(frame_bytes - len(buf))
            if not chunk:
                # True EOF — discard any partial frame at stream end
                if buf:
                    log.warning(
                        "waveform_overlay: partial frame at EOF (%d/%d bytes), discarding",
                        len(buf),
                        frame_bytes,
                    )
                break
            buf.extend(chunk)
            if len(buf) < frame_bytes:
                continue  # need more data to complete this frame
            raw = bytes(buf)
            buf = bytearray()
            video_frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (vid_h, vid_w, 3)
            )
            overlay = _render_overlay_frame(
                video_frame, band_energies, frame_idx, vid_w, vid_h,
            )
            writer_proc.stdin.write(overlay.tobytes())
            frame_idx += 1

        log.info("waveform_overlay: wrote %d frames", frame_idx)
        writer_proc.stdin.close()
        _, stderr_bytes = await writer_proc.communicate()

        # Clean up reader and capture stderr for diagnostics
        _, reader_stderr_bytes = await reader_proc.communicate()

        if reader_proc.returncode != 0:
            reader_stderr = reader_stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg waveform_overlay reader failed (rc={reader_proc.returncode}): {reader_stderr}"
            )

        if writer_proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg waveform_overlay writer failed: {stderr}")

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
