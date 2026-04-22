"""waveform_overlay step executor.

Burns an animated 140px player bar (waveform + progress + album art + title)
into a second video. Stored as WAVEFORM_VIDEO blob alongside the clean video.
"""

from __future__ import annotations

import asyncio
import io
import struct
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.engine import SkippedStepOutput, StepContext
from app.models.enums import BlobType, ChunkStatus
from app.models.json_schemas import WaveformOverlayStepOutput, WorkflowInputSchema
from app.models.workflow import WorkflowBlob, WorkflowScene
from app.services import blob_service
from app.services.workflow_service import (
    get_optional_workflow_id,
    get_scenes_for_workflow,
)
from app.workflows.db_helpers import get_session_maker
from app.workflows.steps.stitch import StitchStepOutput

log = structlog.get_logger(__name__)

# Visual constants
_BAR_H = 140          # overlay bar height in pixels
_VIDEO_W = 1280       # expected video width
_ART_SIZE = 90        # album art thumbnail size
_ART_X = 16          # album art left margin
_ART_Y = 25          # album art top margin
_N_BARS = 120         # number of waveform bars displayed at once
_FINE_STEPS = 2000    # high-resolution envelope for scrolling
_BAR_COLOR_PLAYED = (230, 60, 100)       # pink
_BAR_COLOR_FUTURE = (100, 110, 140)      # gray-blue
_PROGRESS_COLOR = (230, 60, 100)         # pink
_BG_COLOR = (15, 20, 40, 200)           # dark navy ~78% opacity
_TITLE_COLOR = (255, 255, 255)           # white
_SUBTITLE_COLOR = (160, 165, 185)        # gray


def _rounded_thumbnail(data: bytes, size: int) -> Image.Image:
    """Decode image bytes, scale to size×size with rounded corners."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    img = img.resize((size, size), Image.LANCZOS)

    # Rounded corner mask
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = 10
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TTF font, falling back to PIL default if unavailable."""
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _build_static_frame(
    art_img: Image.Image | None,
    title: str,
    subtitle: str,
) -> Image.Image:
    """Build the static part of the overlay frame (1280×140 RGBA).

    Returns a PIL Image with the background, art, text, and controls.
    Waveform bars are drawn dynamically per-frame in _render_frame.
    """
    frame = Image.new("RGBA", (_VIDEO_W, _BAR_H), _BG_COLOR)
    draw = ImageDraw.Draw(frame)

    # --- Album art ---
    art_x = _ART_X
    art_y = _ART_Y
    art_end_x = art_x + _ART_SIZE + 12  # right edge + margin

    if art_img is not None:
        frame.paste(art_img, (art_x, art_y), art_img)

    # --- Text block ---
    text_x = art_end_x
    font_title = _load_font(18, bold=True)
    font_sub = _load_font(13, bold=False)

    draw.text((text_x, 28), title.upper(), font=font_title, fill=_TITLE_COLOR)
    draw.text((text_x, 54), subtitle, font=font_sub, fill=_SUBTITLE_COLOR)

    # --- Control icons ---
    font_ctrl = _load_font(20, bold=False)
    ctrl_x = _VIDEO_W - 90
    ctrl_y = 28
    draw.text((ctrl_x, ctrl_y), "⏮ ▶ ⏭", font=font_ctrl, fill=_TITLE_COLOR)

    # --- Progress track line (bottom) ---
    prog_y = _BAR_H - 8
    draw.line([(0, prog_y), (_VIDEO_W, prog_y)], fill=_BAR_COLOR_FUTURE, width=2)

    return frame


def _render_frame(
    static_arr: np.ndarray[Any, np.dtype[np.uint8]],
    fine_envs: list[float],
    progress: float,
    waveform_x0: int,
    waveform_x1: int,
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Clone static array and draw scrolling waveform bars + progress indicator.

    Shows _N_BARS bars centered at the current playback position. Left half of
    bars (already played) are pink; right half (upcoming) are gray. As progress
    advances the waveform scrolls, giving a dynamic live-waveform appearance.
    """
    frame = static_arr.copy()

    bar_area_w = waveform_x1 - waveform_x0
    bar_w = max(2, bar_area_w // (_N_BARS * 2))
    bar_gap = bar_w
    wave_y_center = 85
    max_bar_h = 28

    n_fine = len(fine_envs)
    # Index in fine_envs corresponding to current playback position
    current_fine = int(progress * (n_fine - 1))
    half_bars = _N_BARS // 2

    for i in range(_N_BARS):
        fine_idx = current_fine + (i - half_bars)
        if 0 <= fine_idx < n_fine:
            env = fine_envs[fine_idx]
        else:
            env = 0.0  # silence for out-of-range positions

        bx = waveform_x0 + i * (bar_w + bar_gap)
        if bx + bar_w > waveform_x1:
            break

        bh = max(3, int(env * max_bar_h))
        y0 = wave_y_center - bh
        y1 = wave_y_center + bh
        # Bars at or before center = played (pink); after center = upcoming (gray)
        color = _BAR_COLOR_PLAYED if i <= half_bars else _BAR_COLOR_FUTURE
        frame[y0:y1, bx : bx + bar_w] = (*color, 255)

    # Progress bar fill
    prog_y = _BAR_H - 8
    fill_w = int(progress * _VIDEO_W)
    frame[prog_y - 1 : prog_y + 1, :fill_w] = (*_PROGRESS_COLOR, 255)

    # Scrubber dot
    dot_x = max(6, min(_VIDEO_W - 6, fill_w))
    dot_r = 5
    for dy in range(-dot_r, dot_r + 1):
        dx = int((dot_r**2 - dy**2) ** 0.5)
        y_pos = prog_y + dy
        if 0 <= y_pos < _BAR_H:
            x0 = max(0, dot_x - dx)
            x1 = min(_VIDEO_W, dot_x + dx)
            frame[y_pos, x0:x1] = (*_PROGRESS_COLOR, 255)

    return frame


async def _ffprobe_video(path: str) -> tuple[float, int, int]:
    """Return (fps, width, height) via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
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
    """Decode audio to mono f32 PCM at 22050 Hz via ffmpeg pipe."""
    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-f", "f32le",
        "-ar", "22050",
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

    # raw f32le bytes → float32 array
    n_samples = len(stdout) // 4
    samples = struct.unpack(f"<{n_samples}f", stdout[:n_samples * 4])
    return np.array(samples, dtype=np.float32)


def _compute_envelope(samples: np.ndarray[Any, np.dtype[np.float32]], n_bars: int) -> list[float]:
    """Compute RMS envelope across n_bars segments, normalized to [0, 1]."""
    seg_len = max(1, len(samples) // n_bars)
    envs: list[float] = []
    for i in range(n_bars):
        seg = samples[i * seg_len : (i + 1) * seg_len]
        rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) > 0 else 0.0
        envs.append(rms)

    max_val = max(envs) if any(envs) else 1.0
    if max_val < 1e-9:
        max_val = 1.0
    return [v / max_val for v in envs]


async def execute(
    input: WorkflowInputSchema, ctx: StepContext
) -> WaveformOverlayStepOutput | SkippedStepOutput:
    """Render animated waveform overlay on the stitched video.

    Produces a second WAVEFORM_VIDEO blob with a 140px animated player bar
    burned in at the bottom. Skips if stitch_final produced no video.

    Args:
        input: Workflow input (premise, voice_name, stitch_video).
        ctx: Step context with workflow_run_id and parent outputs.

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

    log.info(
        "waveform_overlay: starting workflow_id=%s video=%s",
        workflow_run_id,
        video_blob_id,
    )

    # --- Metadata ---
    title = input.premise[:60]  # fallback title from premise
    subtitle = input.premise[:40]

    # Try to get story title from DB
    async with session_maker() as session:
        from sqlalchemy import text as sa_text
        story_row = await session.execute(
            sa_text(
                "SELECT title FROM stories WHERE workflow_id = :wf_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"wf_id": workflow_id},
        )
        story_rec = story_row.first()
        if story_rec and story_rec[0]:
            title = str(story_rec[0])

    # --- Album art: first completed scene ---
    art_img: Image.Image | None = None
    async with session_maker() as session:
        scenes = await get_scenes_for_workflow(session, workflow_id)

    completed = [s for s in scenes if s.image_blob_id is not None]
    if completed:
        async with session_maker() as session:
            art_blob = await blob_service.get(session, completed[0].image_blob_id)
        try:
            art_img = _rounded_thumbnail(art_blob.data, _ART_SIZE)
        except Exception:
            log.warning("waveform_overlay: failed to decode album art, skipping")

    # --- Fetch video + audio blobs → temp files ---
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

        # --- ffprobe ---
        fps, vid_w, vid_h = await _ffprobe_video(str(video_in))
        log.info("waveform_overlay: video %dx%d @%.2ffps", vid_w, vid_h, fps)

        # --- Decode audio + compute fine-resolution envelope for scrolling ---
        samples = await _decode_audio_f32(str(audio_in))
        fine_envs = _compute_envelope(samples, _FINE_STEPS)

        # --- Pre-render static frame (no bars — drawn per-frame) ---
        static_pil = _build_static_frame(art_img, title, subtitle)
        static_arr = np.array(static_pil, dtype=np.uint8)

        # --- Compute frame count from video duration ---
        duration_sec = stitch_out.total_duration_sec
        total_frames = max(1, int(duration_sec * fps))

        # --- Text x0/x1 for waveform region (mirrors _build_static_frame) ---
        art_end_x = _ART_X + _ART_SIZE + 12
        ctrl_x = _VIDEO_W - 90
        waveform_x0 = art_end_x
        waveform_x1 = ctrl_x - 20

        # --- Launch ffmpeg to read input video + PNG pipe as overlay ---
        bar_h = _BAR_H
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_in),
            "-f", "image2pipe",
            "-framerate", f"{fps:.6f}",
            "-vcodec", "png",
            "-i", "pipe:0",
            "-filter_complex",
            (
                f"[0:v]split[v1][v2];"
                f"[v2]crop=iw:{bar_h}:0:ih-{bar_h},"
                f"boxblur=luma_radius=15:luma_power=2[blurred];"
                f"[v1][blurred]overlay=0:H-{bar_h}[bgblur];"
                f"[bgblur][1:v]overlay=0:H-{bar_h}:format=auto[final]"
            ),
            "-map", "[final]",
            "-map", "0:a",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(video_out),
        ]

        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert proc.stdin is not None

        # Stream PNG frames to ffmpeg stdin
        for frame_idx in range(total_frames):
            progress = frame_idx / max(1, total_frames - 1)
            frame_arr = _render_frame(
                static_arr, fine_envs, progress, waveform_x0, waveform_x1
            )
            pil_frame = Image.fromarray(frame_arr, mode="RGBA")
            buf = io.BytesIO()
            pil_frame.save(buf, format="PNG")
            proc.stdin.write(buf.getvalue())

        proc.stdin.close()
        _, stderr_bytes = await proc.communicate()

        if proc.returncode != 0:
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
