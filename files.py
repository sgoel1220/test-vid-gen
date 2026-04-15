"""File-system helpers: voice/reference scanning, audio validation, PerformanceMonitor."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import soundfile as sf

from config import get_predefined_voices_path, get_reference_audio_path

logger = logging.getLogger(__name__)

ALLOWED_AUDIO_EXTENSIONS = (".wav", ".mp3")


# ---------------------------------------------------------------------------
# Reference audio
# ---------------------------------------------------------------------------

def get_valid_reference_files() -> List[str]:
    """Return sorted list of valid audio files in the reference_audio directory."""
    ref_dir = get_reference_audio_path()
    files: list[str] = []
    try:
        if ref_dir.is_dir():
            files = [
                item.name
                for item in ref_dir.iterdir()
                if item.is_file()
                and not item.name.startswith(".")
                and item.suffix.lower() in ALLOWED_AUDIO_EXTENSIONS
            ]
        else:
            logger.warning("Reference audio directory not found: %s. Creating it.", ref_dir)
            ref_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.error("Error reading reference audio directory '%s': %s", ref_dir, exc, exc_info=True)
    return sorted(files)


def validate_reference_audio(
    file_path: Path, max_duration_sec: Optional[int] = None
) -> Tuple[bool, str]:
    """Check existence, extension, and optionally duration of a reference file."""
    if not file_path.exists() or not file_path.is_file():
        return False, f"Reference audio file not found at: {file_path}"
    if file_path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return False, "Invalid reference audio file type. Please use WAV or MP3 format."
    if max_duration_sec is not None and max_duration_sec > 0:
        try:
            info = sf.info(str(file_path))
            if info.duration <= 0:
                return False, f"Reference audio '{file_path.name}' has zero or negative duration."
            if info.duration > max_duration_sec:
                return (
                    False,
                    f"Reference audio duration ({info.duration:.2f}s) exceeds maximum ({max_duration_sec}s).",
                )
        except Exception as exc:
            logger.warning(
                "Could not determine duration of '%s': %s. Skipping duration check.",
                file_path.name, exc,
            )
    return True, "Reference audio appears valid."


# ---------------------------------------------------------------------------
# Predefined voices
# ---------------------------------------------------------------------------

def get_predefined_voices() -> List[Dict[str, str]]:
    """Return sorted list of predefined voices with display names and filenames."""
    voices_dir = get_predefined_voices_path()
    result: list[dict[str, str]] = []
    try:
        if not voices_dir.is_dir():
            logger.warning("Predefined voices directory not found: %s. Creating it.", voices_dir)
            voices_dir.mkdir(parents=True, exist_ok=True)
            return []

        raw: list[dict[str, str]] = []
        for item in voices_dir.iterdir():
            if item.is_file() and not item.name.startswith(".") and item.suffix.lower() in ALLOWED_AUDIO_EXTENSIONS:
                words = item.stem.replace("_", " ").replace("-", " ").split()
                display = " ".join(w.capitalize() for w in words) or item.stem
                raw.append({"original_filename": item.name, "display_base": display})

        raw.sort(key=lambda x: x["display_base"].lower())
        counts: dict[str, int] = {}
        for voice in raw:
            base = voice["display_base"]
            if base in counts:
                counts[base] += 1
                name = f"{base} ({counts[base]})"
            else:
                counts[base] = 1
                name = base
            result.append({"display_name": name, "filename": voice["original_filename"]})

        result.sort(key=lambda x: x["display_name"].lower())
        logger.info("Found %d predefined voices in %s.", len(result), voices_dir)
    except Exception as exc:
        logger.error("Error reading predefined voices directory '%s': %s", voices_dir, exc, exc_info=True)
    return result


# ---------------------------------------------------------------------------
# Performance monitoring
# ---------------------------------------------------------------------------

class PerformanceMonitor:
    """Record and report elapsed time for named events."""

    def __init__(self, enabled: bool = True, logger_instance: Optional[logging.Logger] = None):
        self.enabled = enabled
        self._log = logger_instance or logging.getLogger(__name__)
        self._start = 0.0
        self._events: list[tuple[str, float]] = []
        if enabled:
            self._start = time.monotonic()
            self._events.append(("started", self._start))

    def record(self, event: str) -> None:
        if self.enabled:
            self._events.append((event, time.monotonic()))

    def report(self, log_level: int = logging.DEBUG) -> str:
        if not self.enabled or not self._events:
            return "Performance monitoring disabled or no events recorded."
        lines = ["Performance Report:"]
        prev_t = self._events[0][1]
        for name, t in self._events[1:]:
            lines.append(f"  {name}: +{t - prev_t:.4f}s (total {t - self._start:.4f}s)")
            prev_t = t
        if len(self._events) > 1:
            lines.append(f"Total: {self._events[-1][1] - self._start:.4f}s")
        report = "\n".join(lines)
        self._log.log(log_level, report)
        return report
