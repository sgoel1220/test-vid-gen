"""Audio encoding utilities using ffmpeg subprocess."""

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import numpy.typing as npt


async def encode_wav_to_mp3(
    audio: npt.NDArray[np.float32],
    sample_rate: int,
    bitrate: str = "192k",
) -> bytes:
    """Encode float32 PCM audio to MP3 using ffmpeg.

    Args:
        audio: Float32 audio samples, shape (samples,) or (samples, channels).
        sample_rate: Sample rate in Hz.
        bitrate: MP3 bitrate (default 192k).

    Returns:
        MP3-encoded bytes.

    Raises:
        RuntimeError: If ffmpeg encoding fails.
    """
    # Normalize to int16 for ffmpeg raw input
    # Clip to [-1, 1] range first to prevent overflow
    audio_clipped = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio_clipped * 32767).astype(np.int16)

    # Determine number of channels
    if audio_int16.ndim == 1:
        channels = 1
    else:
        channels = audio_int16.shape[1]

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Use ffmpeg with raw PCM input piped via stdin
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-f", "s16le",  # Input format: signed 16-bit little-endian
            "-ar", str(sample_rate),  # Input sample rate
            "-ac", str(channels),  # Input channels
            "-i", "pipe:0",  # Read from stdin
            "-b:a", bitrate,  # Output bitrate
            str(tmp_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate(input=audio_int16.tobytes())

        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg encoding failed: {stderr}")

        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
