"""AudioStore protocol and LocalFilesystemAudioStore implementation."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiofiles


@runtime_checkable
class AudioStore(Protocol):
    async def put(self, data: bytes, ext: str) -> tuple[str, str, int]:
        """Store audio bytes atomically.

        Returns:
            (storage_key, sha256_hex, byte_size)
        """
        ...

    def stream(self, storage_key: str) -> AsyncIterator[bytes]:
        """Yield 64 KiB chunks of the stored audio file."""
        ...


# Module-level singleton — set during lifespan startup via set_audio_store().
_audio_store: AudioStore | None = None


def set_audio_store(store: AudioStore) -> None:
    global _audio_store
    _audio_store = store


def get_audio_store() -> AudioStore:
    assert _audio_store is not None, "audio store not initialised"
    return _audio_store


class LocalFilesystemAudioStore:
    """Stores audio under {root}/{yyyy}/{mm}/{dd}/{uuid}.{ext}.

    Writes are atomic: bytes land in a .tmp file first, then renamed.
    Streaming reads use aiofiles so the event loop is not blocked.
    """

    _CHUNK_SIZE = 64 * 1024  # 64 KiB

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _resolve(self, storage_key: str) -> Path:
        return self._root / storage_key

    async def put(self, data: bytes, ext: str) -> tuple[str, str, int]:
        now = datetime.now(tz=timezone.utc)
        rel_dir = Path(f"{now.year:04d}") / f"{now.month:02d}" / f"{now.day:02d}"
        filename = f"{uuid.uuid4()}.{ext}"
        dest = self._root / rel_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        async with aiofiles.open(tmp, "wb") as fh:
            await fh.write(data)
        tmp.rename(dest)  # atomic on POSIX
        sha256 = hashlib.sha256(data).hexdigest()
        storage_key = str(rel_dir / filename)
        return storage_key, sha256, len(data)

    async def stream(self, storage_key: str) -> AsyncIterator[bytes]:
        path = self._resolve(storage_key)
        async with aiofiles.open(path, "rb") as fh:
            while True:
                chunk = await fh.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
