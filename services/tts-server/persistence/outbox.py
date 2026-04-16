"""SQLite-backed write-ahead outbox for durable, causal-ordered persistence events.

Design
------
All persistence calls from the pod go through :class:`Outbox` instead of calling
``PersistenceClient`` directly.  A background :meth:`Outbox.background_drain_loop`
replays queued rows every 5 s, applying exponential back-off on transient errors.

Causal ordering
---------------
Rows are drained in kind-priority order so the metadata server always receives
events in a logically valid sequence::

    create_script → create_run → create_chunks
    → upload_chunk_audio → upload_final_audio → patch_run

This guarantees, e.g., that ``create_run`` is never dispatched before the
corresponding ``create_script`` row has been confirmed.

Blob handling
-------------
For audio uploads the raw bytes are written to a side file under
``outputs/persistence_outbox/<uuid>.bin`` so the SQLite row stays small.
On success the row *and* the blob file are deleted atomically (best-effort).
On permanent failure the row is left in place for manual inspection; the blob
file is also left so the data is not lost.

Lifecycle (caller's responsibility)
------------------------------------
1. ``await outbox.open()`` on pod startup.
2. ``await outbox.drain_once(client)`` immediately after opening (replays
   anything the previous pod left behind).
3. ``asyncio.create_task(outbox.background_drain_loop(client))`` to start the
   background loop.
4. Cancel the task and call ``await outbox.aclose()`` on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from pydantic import BaseModel

from creepy_pasta_protocol.chunks import ChunkSpec
from creepy_pasta_protocol.common import AudioFormat
from creepy_pasta_protocol.runs import CreateRunRequest, PatchRunRequest
from creepy_pasta_protocol.validation import ChunkValidationSnapshot

from persistence.client import PersistenceClient
from persistence.errors import PermanentPersistenceError, TransientPersistenceError

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filesystem paths
# ---------------------------------------------------------------------------

_OUTBOX_DB: Path = Path("outputs/persistence_outbox.sqlite")
_BLOB_DIR: Path = Path("outputs/persistence_outbox")

# ---------------------------------------------------------------------------
# Kind constants and causal priority
# ---------------------------------------------------------------------------

KIND_CREATE_SCRIPT = "create_script"
KIND_CREATE_RUN = "create_run"
KIND_CREATE_CHUNKS = "create_chunks"
KIND_UPLOAD_CHUNK_AUDIO = "upload_chunk_audio"
KIND_UPLOAD_FINAL_AUDIO = "upload_final_audio"
KIND_PATCH_RUN = "patch_run"

# Used in the drain ORDER BY to enforce causal ordering.
_KIND_PRIORITY: dict[str, int] = {
    KIND_CREATE_SCRIPT: 0,
    KIND_CREATE_RUN: 1,
    KIND_CREATE_CHUNKS: 2,
    KIND_UPLOAD_CHUNK_AUDIO: 3,
    KIND_UPLOAD_FINAL_AUDIO: 4,
    KIND_PATCH_RUN: 5,
}

_DRAIN_SQL = """
    SELECT id, kind, payload_json, blob_path, attempts
    FROM   outbox
    WHERE  next_attempt_at <= ?
    ORDER BY
        CASE kind
            WHEN 'create_script'      THEN 0
            WHEN 'create_run'         THEN 1
            WHEN 'create_chunks'      THEN 2
            WHEN 'upload_chunk_audio' THEN 3
            WHEN 'upload_final_audio' THEN 4
            WHEN 'patch_run'          THEN 5
            ELSE 99
        END,
        id
"""

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS outbox (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        kind            TEXT    NOT NULL,
        payload_json    TEXT    NOT NULL,
        blob_path       TEXT,
        attempts        INTEGER NOT NULL DEFAULT 0,
        last_error      TEXT,
        created_at      TEXT    NOT NULL,
        next_attempt_at TEXT    NOT NULL
    )
"""

# ---------------------------------------------------------------------------
# Internal payload schemas
# These are private to outbox.py — NOT wire schemas; do not add to protocol.
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    model_config = {"frozen": True}  # type: ignore[assignment]


class _CreateScriptPayload(_Base):
    text: str


class _CreateRunPayload(_Base):
    request: CreateRunRequest


class _PatchRunPayload(_Base):
    run_id: str
    request: PatchRunRequest


class _CreateChunksPayload(_Base):
    run_id: str
    specs: list[ChunkSpec]


class _UploadChunkAudioPayload(_Base):
    run_id: str
    chunk_index: int
    format: AudioFormat
    sample_rate: int
    duration_sec: float
    mime_type: str
    attempts_used: int = 0
    validation: Optional[ChunkValidationSnapshot] = None


class _UploadFinalAudioPayload(_Base):
    run_id: str
    format: AudioFormat
    sample_rate: int
    duration_sec: float
    mime_type: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_attempt_at(attempts: int) -> str:
    """Exponential back-off capped at 60 s: delay = min(60, 2^attempts)."""
    delay = min(60, 2**attempts)
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()


def _delete_blob(blob_path: str) -> None:
    try:
        Path(blob_path).unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("outbox: could not delete blob %s: %s", blob_path, exc)


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


class Outbox:
    """SQLite-backed outbox for resilient, causally-ordered persistence events.

    Usage::

        async with Outbox() as ob:
            await ob.drain_once(client)          # replay leftovers from prev pod
            task = asyncio.create_task(
                ob.background_drain_loop(client)
            )
            ...
            task.cancel()
    """

    def __init__(
        self,
        db_path: Path = _OUTBOX_DB,
        blob_dir: Path = _BLOB_DIR,
    ) -> None:
        self._db_path = db_path
        self._blob_dir = blob_dir
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open (or create) the SQLite database and ensure the table exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self._db_path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.commit()
        self._conn = conn
        _log.info("outbox opened: db=%s", self._db_path)

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "Outbox":
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _conn_or_raise(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Outbox is not open — call open() first.")
        return self._conn

    # ------------------------------------------------------------------
    # Low-level enqueue
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        kind: str,
        payload: BaseModel,
        blob_bytes: Optional[bytes] = None,
    ) -> int:
        """Insert one outbox row and return its row id.

        If *blob_bytes* is provided it is written to a side file; the row
        stores only the path.
        """
        conn = self._conn_or_raise()
        now = _utcnow()
        blob_path: Optional[str] = None

        if blob_bytes is not None:
            blob_file = self._blob_dir / f"{uuid.uuid4().hex}.bin"
            blob_file.write_bytes(blob_bytes)
            blob_path = str(blob_file)

        cursor = await conn.execute(
            """
            INSERT INTO outbox
                (kind, payload_json, blob_path, attempts, created_at, next_attempt_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (kind, payload.model_dump_json(), blob_path, now, now),
        )
        await conn.commit()
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        _log.debug("outbox enqueue: id=%d kind=%s", row_id, kind)
        return row_id

    # ------------------------------------------------------------------
    # Typed enqueue helpers
    # ------------------------------------------------------------------

    async def enqueue_create_script(self, text: str) -> int:
        return await self.enqueue(KIND_CREATE_SCRIPT, _CreateScriptPayload(text=text))

    async def enqueue_create_run(self, request: CreateRunRequest) -> int:
        return await self.enqueue(KIND_CREATE_RUN, _CreateRunPayload(request=request))

    async def enqueue_patch_run(self, run_id: str, request: PatchRunRequest) -> int:
        return await self.enqueue(
            KIND_PATCH_RUN, _PatchRunPayload(run_id=run_id, request=request)
        )

    async def enqueue_create_chunks(self, run_id: str, specs: list[ChunkSpec]) -> int:
        return await self.enqueue(
            KIND_CREATE_CHUNKS, _CreateChunksPayload(run_id=run_id, specs=specs)
        )

    async def enqueue_upload_chunk_audio(
        self,
        run_id: str,
        chunk_index: int,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
        attempts_used: int = 0,
        validation: Optional[ChunkValidationSnapshot] = None,
    ) -> int:
        payload = _UploadChunkAudioPayload(
            run_id=run_id,
            chunk_index=chunk_index,
            format=format,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            mime_type=mime_type,
            attempts_used=attempts_used,
            validation=validation,
        )
        return await self.enqueue(KIND_UPLOAD_CHUNK_AUDIO, payload, blob_bytes=data)

    async def enqueue_upload_final_audio(
        self,
        run_id: str,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
    ) -> int:
        payload = _UploadFinalAudioPayload(
            run_id=run_id,
            format=format,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            mime_type=mime_type,
        )
        return await self.enqueue(KIND_UPLOAD_FINAL_AUDIO, payload, blob_bytes=data)

    # ------------------------------------------------------------------
    # Drain
    # ------------------------------------------------------------------

    async def drain_once(self, client: PersistenceClient) -> int:
        """Dispatch all ready rows to the metadata server.

        A row is *ready* when its ``next_attempt_at`` is in the past.
        Rows are dispatched in causal-priority order (create_script first,
        patch_run last) and then by insertion order within the same kind.

        Returns the number of rows that were attempted (succeeded or failed).
        Permanently-failed rows are left in the table for manual inspection.
        """
        conn = self._conn_or_raise()
        now = _utcnow()

        async with conn.execute(_DRAIN_SQL, (now,)) as cur:
            rows = await cur.fetchall()

        attempted = 0
        for row in rows:
            row_id: int = row["id"]
            kind: str = row["kind"]
            payload_json: str = row["payload_json"]
            blob_path: Optional[str] = row["blob_path"]
            attempts: int = row["attempts"]
            attempted += 1

            try:
                await self._dispatch(client, kind, payload_json, blob_path)
            except TransientPersistenceError as exc:
                new_attempts = attempts + 1
                await conn.execute(
                    """
                    UPDATE outbox
                    SET attempts = ?, last_error = ?, next_attempt_at = ?
                    WHERE id = ?
                    """,
                    (new_attempts, str(exc), _next_attempt_at(new_attempts), row_id),
                )
                await conn.commit()
                _log.warning(
                    "outbox transient error: id=%d kind=%s attempt=%d: %s",
                    row_id,
                    kind,
                    new_attempts,
                    exc,
                )
                continue
            except PermanentPersistenceError as exc:
                new_attempts = attempts + 1
                await conn.execute(
                    "UPDATE outbox SET attempts = ?, last_error = ? WHERE id = ?",
                    (new_attempts, str(exc), row_id),
                )
                await conn.commit()
                _log.warning(
                    "outbox permanent error (manual intervention required): "
                    "id=%d kind=%s: %s",
                    row_id,
                    kind,
                    exc,
                )
                continue

            # Success: remove the row and its blob file.
            await conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            await conn.commit()
            if blob_path:
                _delete_blob(blob_path)
            _log.info("outbox drained: id=%d kind=%s", row_id, kind)

        return attempted

    async def _dispatch(
        self,
        client: PersistenceClient,
        kind: str,
        payload_json: str,
        blob_path: Optional[str],
    ) -> None:
        """Reconstruct the payload and call the appropriate client method."""
        if kind == KIND_CREATE_SCRIPT:
            p = _CreateScriptPayload.model_validate_json(payload_json)
            await client.create_or_get_script(p.text)

        elif kind == KIND_CREATE_RUN:
            p2 = _CreateRunPayload.model_validate_json(payload_json)
            await client.create_run(p2.request)

        elif kind == KIND_PATCH_RUN:
            p3 = _PatchRunPayload.model_validate_json(payload_json)
            await client.patch_run_by_pod(p3.run_id, p3.request)

        elif kind == KIND_CREATE_CHUNKS:
            p4 = _CreateChunksPayload.model_validate_json(payload_json)
            await client.create_chunks_by_pod(p4.run_id, list(p4.specs))

        elif kind == KIND_UPLOAD_CHUNK_AUDIO:
            p5 = _UploadChunkAudioPayload.model_validate_json(payload_json)
            if blob_path is None:
                raise PermanentPersistenceError(
                    f"upload_chunk_audio row id missing blob_path"
                )
            data = Path(blob_path).read_bytes()
            await client.upload_chunk_audio_by_pod(
                pod_run_id=p5.run_id,
                chunk_index=p5.chunk_index,
                data=data,
                format=p5.format,
                sample_rate=p5.sample_rate,
                duration_sec=p5.duration_sec,
                mime_type=p5.mime_type,
                attempts_used=p5.attempts_used,
                validation=p5.validation,
            )

        elif kind == KIND_UPLOAD_FINAL_AUDIO:
            p6 = _UploadFinalAudioPayload.model_validate_json(payload_json)
            if blob_path is None:
                raise PermanentPersistenceError(
                    f"upload_final_audio row missing blob_path"
                )
            data = Path(blob_path).read_bytes()
            await client.upload_final_audio_by_pod(
                pod_run_id=p6.run_id,
                data=data,
                format=p6.format,
                sample_rate=p6.sample_rate,
                duration_sec=p6.duration_sec,
                mime_type=p6.mime_type,
            )

        else:
            raise PermanentPersistenceError(f"Unknown outbox kind: {kind!r}")

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def background_drain_loop(
        self,
        client: PersistenceClient,
        interval: float = 5.0,
    ) -> None:
        """Drain the outbox every *interval* seconds until cancelled.

        Run as an ``asyncio.Task``::

            task = asyncio.create_task(outbox.background_drain_loop(client))
            ...
            task.cancel()   # on shutdown
        """
        _log.info("outbox drain loop started (interval=%.1f s)", interval)
        while True:
            try:
                n = await self.drain_once(client)
                if n:
                    _log.debug("outbox drain pass: %d row(s) attempted", n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.error(
                    "outbox drain loop unexpected error: %s", exc, exc_info=True
                )
            await asyncio.sleep(interval)
