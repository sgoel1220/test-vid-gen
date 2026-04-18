"""Audio encoding — Strategy pattern.

Each format is handled by a dedicated encoder class.  The public API
(``encode_audio``, ``save_audio_to_file``, ``save_audio_tensor_to_file``)
delegates to the appropriate encoder via ``ENCODERS``.
"""

from __future__ import annotations

import io
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torchaudio
import torch

from enums import AudioFormat

try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    librosa = None  # type: ignore[assignment]
    LIBROSA_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float32_mono(audio_array: np.ndarray) -> np.ndarray:
    """Normalise to float32 mono in-place-free manner."""
    if audio_array.dtype != np.float32:
        if np.issubdtype(audio_array.dtype, np.integer):
            audio_array = audio_array.astype(np.float32) / np.iinfo(audio_array.dtype).max
        else:
            audio_array = audio_array.astype(np.float32)
    if audio_array.ndim == 2 and audio_array.shape[1] == 1:
        audio_array = audio_array.squeeze(axis=1)
    elif audio_array.ndim > 1:
        logger.warning("Multi-channel audio — using first channel only.")
        audio_array = audio_array[:, 0]
    return audio_array


def _resample_if_needed(
    audio: np.ndarray, src_rate: int, dst_rate: Optional[int]
) -> tuple[np.ndarray, int]:
    if dst_rate is None or dst_rate == src_rate:
        return audio, src_rate
    if LIBROSA_AVAILABLE:
        try:
            audio = librosa.resample(y=audio, orig_sr=src_rate, target_sr=dst_rate)
            return audio, dst_rate
        except Exception as exc:
            logger.error("Resampling to %dHz failed: %s. Using original rate.", dst_rate, exc)
    else:
        logger.warning("Librosa unavailable — cannot resample %d→%d.", src_rate, dst_rate)
    return audio, src_rate


# ---------------------------------------------------------------------------
# Strategy: per-format encoders
# ---------------------------------------------------------------------------

class AudioEncoder(ABC):
    @abstractmethod
    def encode(
        self,
        audio: np.ndarray,
        sample_rate: int,
        target_sample_rate: Optional[int] = None,
    ) -> Optional[bytes]: ...


class WavEncoder(AudioEncoder):
    def encode(
        self,
        audio: np.ndarray,
        sample_rate: int,
        target_sample_rate: Optional[int] = None,
    ) -> Optional[bytes]:
        audio, sample_rate = _resample_if_needed(audio, sample_rate, target_sample_rate)
        buf = io.BytesIO()
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        sf.write(buf, audio_int16, sample_rate, format="wav", subtype="pcm_16")
        return buf.getvalue()


class OpusEncoder(AudioEncoder):
    _SUPPORTED_RATES = {8000, 12000, 16000, 24000, 48000}
    _TARGET_RATE = 48000

    def encode(
        self,
        audio: np.ndarray,
        sample_rate: int,
        target_sample_rate: Optional[int] = None,
    ) -> Optional[bytes]:
        audio, sample_rate = _resample_if_needed(audio, sample_rate, target_sample_rate)
        if sample_rate not in self._SUPPORTED_RATES:
            audio, sample_rate = _resample_if_needed(audio, sample_rate, self._TARGET_RATE)
        buf = io.BytesIO()
        sf.write(buf, audio, sample_rate, format="ogg", subtype="opus")
        return buf.getvalue()


class Mp3Encoder(AudioEncoder):
    def encode(
        self,
        audio: np.ndarray,
        sample_rate: int,
        target_sample_rate: Optional[int] = None,
    ) -> Optional[bytes]:
        from pydub import AudioSegment  # lazy import — pydub is optional

        audio, sample_rate = _resample_if_needed(audio, sample_rate, target_sample_rate)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        segment = AudioSegment(
            audio_int16.tobytes(), frame_rate=sample_rate, sample_width=2, channels=1
        )
        buf = io.BytesIO()
        segment.export(buf, format="mp3")
        return buf.getvalue()


ENCODERS: dict[AudioFormat, AudioEncoder] = {
    AudioFormat.WAV: WavEncoder(),
    AudioFormat.OPUS: OpusEncoder(),
    AudioFormat.MP3: Mp3Encoder(),
}

WAV_MEDIA_TYPE = "audio/wav"


def encode_to_wav_bytes(wav_tensor: torch.Tensor, sample_rate: int) -> bytes:
    """Convert a synthesis tensor to WAV bytes (PCM-16)."""
    audio_np: np.ndarray = wav_tensor.squeeze().cpu().numpy()
    audio_int16 = (np.clip(audio_np, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, audio_int16, sample_rate, format="wav", subtype="PCM_16")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode_audio(
    audio_array: np.ndarray,
    sample_rate: int,
    output_format: AudioFormat = AudioFormat.OPUS,
    target_sample_rate: Optional[int] = None,
) -> Optional[bytes]:
    """Encode a NumPy audio array to the requested format and return bytes."""
    if audio_array is None or audio_array.size == 0:
        logger.warning("encode_audio received empty or None audio array.")
        return None

    audio = _to_float32_mono(audio_array)
    encoder = ENCODERS.get(output_format)
    if encoder is None:
        logger.error("Unsupported output format: %s", output_format)
        return None

    start = time.time()
    try:
        data = encoder.encode(audio, sample_rate, target_sample_rate)
        logger.info(
            "Encoded %d bytes to '%s' in %.3fs.",
            len(data) if data else 0,
            output_format.value,
            time.time() - start,
        )
        return data
    except ImportError as exc:
        logger.critical("Missing library for %s encoding: %s", output_format.value, exc)
        return None
    except Exception as exc:
        logger.error("Error encoding audio to '%s': %s", output_format.value, exc, exc_info=True)
        return None


def save_audio_to_file(
    audio_array: np.ndarray, sample_rate: int, file_path_str: str
) -> bool:
    """Save a NumPy float32 audio array to a WAV file."""
    if audio_array is None or audio_array.size == 0:
        logger.warning("save_audio_to_file received empty or None audio array.")
        return False

    file_path = Path(file_path_str)
    if file_path.suffix.lower() != ".wav":
        logger.warning("save_audio_to_file only supports WAV; got '%s'.", file_path.suffix)

    start = time.time()
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        audio = _to_float32_mono(audio_array)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        sf.write(str(file_path), audio_int16, sample_rate, format="wav", subtype="pcm_16")
        logger.info("Saved WAV to %s in %.3fs.", file_path, time.time() - start)
        return True
    except ImportError:
        logger.critical("SoundFile library not found. Cannot save audio.")
        return False
    except Exception as exc:
        logger.error("Error saving WAV to %s: %s", file_path, exc, exc_info=True)
        return False


def save_audio_tensor_to_file(
    audio_tensor: torch.Tensor,
    sample_rate: int,
    file_path_str: str,
    output_format: AudioFormat = AudioFormat.WAV,
) -> bool:
    """Save a PyTorch audio tensor to a file using torchaudio."""
    if audio_tensor is None or audio_tensor.numel() == 0:
        logger.warning("save_audio_tensor_to_file received empty or None audio tensor.")
        return False

    file_path = Path(file_path_str)
    start = time.time()
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tensor_cpu = audio_tensor.cpu()
        if tensor_cpu.ndim == 1:
            tensor_cpu = tensor_cpu.unsqueeze(0)
        torchaudio.save(str(file_path), tensor_cpu, sample_rate, format=output_format.value)
        logger.info(
            "Saved audio tensor to %s (%s) in %.3fs.",
            file_path, output_format.value, time.time() - start,
        )
        return True
    except Exception as exc:
        logger.error("Error saving tensor to %s: %s", file_path, exc, exc_info=True)
        return False
