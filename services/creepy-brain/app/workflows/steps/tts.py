"""tts_synthesis step executor.

Pipeline:
  1. Fetch story full_text from Postgres using story_id from generate_story output
  2. Normalize full text via LLM API (result cached in-process by text hash)
  3. Chunk normalized text into TTS-ready pieces
  4. Spin up TTS GPU pod (idempotent by workflow_id)
  5. Wait for pod ready
  6. For each chunk (sequential):
     a. POST /synthesize { text, voice, seed } → WAV bytes
     b. validate_chunk_audio(wav_bytes) — runs in creepy-brain, not on the GPU pod
     c. Retry with seed + attempt if validation fails (up to max_chunk_retries)
     d. Save best-effort WAV blob to Postgres (chunk marked FAILED if all attempts fail)
     e. Update workflow_chunks row for per-chunk progress visibility
  7. Terminate the TTS pod (success or failure)
  8. Return chunk blob IDs, count, and total duration

GPU pod contract (stateless /synthesize endpoint):
  POST /synthesize
  Body: { text: str, voice: str, seed: int }
  Response: WAV bytes (Content-Type: audio/wav), HTTP 200 always

  The minimal TTS server (ghcr.io/sgoel1220/tts-server:main) exposes only
  /synthesize and /health endpoints. All text normalization, chunking, and
  audio validation is handled by creepy-brain.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from hatchet_sdk import Context

import app.db as _db
from app.audio.validation import validate_chunk_audio
from app.config import settings
from app.gpu import GpuPodSpec, get_provider
from app.models.enums import BlobType, ChunkStatus
from app.models.schemas import WorkflowInputSchema
from app.services import blob_service
from app.services.story_service import StoryService
from app.services.workflow_service import WorkflowService
from app.text.chunking import chunk_text_by_sentences
from app.text.normalization import normalize_text

log = logging.getLogger(__name__)

_SYNTHESIZE_PATH = "/synthesize"

# Synthesis parameters
_BASE_SEED = 42
_MAX_CHUNK_RETRIES = 2       # up to 3 total attempts per chunk
_CHUNK_SIZE = 800            # max characters per chunk
_POD_TIMEOUT_SEC = 300       # 5 minutes to wait for pod ready
_SYNTHESIZE_TIMEOUT_SEC = 120


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Synthesize audio for the story via a TTS GPU pod.

    Args:
        input: Validated workflow input (contains voice_name).
        ctx: Hatchet execution context (provides workflow_run_id and parent outputs).

    Returns:
        dict with keys: pod_id, chunk_count, total_duration_sec, chunks
    """
    # --- 1. Get story_id from parent step output, then fetch full_text from DB ---
    # ctx._data.parents is a dict[str, Any] keyed by task name; accessing it
    # directly avoids a circular import with content_pipeline.py.
    # full_text is intentionally NOT serialized into the step output to avoid
    # passing large text through Hatchet; we read it directly from Postgres.
    parents: dict[str, Any] = ctx._data.parents
    story_output: dict[str, Any] = parents.get("generate_story", {})
    story_id_raw: uuid.UUID | str | None = story_output.get("story_id")

    if not story_id_raw:
        raise ValueError("generate_story step did not produce story_id")

    # Hatchet may deserialize parent output as a plain dict (JSON path, story_id
    # is a str) or preserve the native Python type (story_id is uuid.UUID).
    # Accept both to guard against the internal _data.parents shape changing.
    story_id_for_text: uuid.UUID = (
        story_id_raw
        if isinstance(story_id_raw, uuid.UUID)
        else uuid.UUID(str(story_id_raw))
    )
    story_id_str: str = str(story_id_for_text)

    _session_maker = _db.async_session_maker
    assert _session_maker is not None, (
        "DB not initialized — call init_db() before starting the Hatchet worker"
    )
    async with _session_maker() as _session:
        _story = await StoryService(_session).get(story_id_for_text)

    if _story is None:
        raise ValueError(f"Story {story_id_for_text} not found in database")
    if not _story.full_text:
        raise ValueError(
            f"Story {story_id_for_text} has no full_text — pipeline may not have completed"
        )
    full_text: str = _story.full_text

    workflow_run_id: str = ctx.workflow_run_id
    voice_name: str = input.voice_name

    log.info(
        "tts_synthesis started workflow_id=%s story_id=%s voice=%s text_len=%d",
        workflow_run_id, story_id_str, voice_name, len(full_text),
    )

    # --- 2. Normalize full text (LLM call, cached in-process by text hash) ---
    normalized_text = await normalize_text(full_text)

    # --- 3. Chunk normalized text ---
    chunks: list[str] = chunk_text_by_sentences(normalized_text, chunk_size=_CHUNK_SIZE)
    if not chunks:
        raise ValueError("text chunking produced zero chunks")

    log.info("tts_synthesis: %d chunks to synthesize", len(chunks))

    # --- 4. Spin up TTS GPU pod ---
    provider = get_provider(settings.runpod_api_key)
    pod = await provider.create_pod(
        spec=GpuPodSpec.from_config(),
        idempotency_key=f"tts-{workflow_run_id}",
    )
    log.info("tts pod created pod_id=%s provider=%s", pod.id, pod.provider)

    # --- 5. Wait for pod ready, then synthesize all chunks ---
    # Pod is terminated in the finally block regardless of success or failure.
    try:
        pod = await provider.wait_for_ready(pod.id, timeout_sec=_POD_TIMEOUT_SEC)
        assert pod.endpoint_url is not None, f"pod {pod.id} ready but has no endpoint_url"
        log.info("tts pod ready endpoint=%s", pod.endpoint_url)

        chunk_results, total_duration_sec = await _synthesize_all_chunks(
            endpoint_url=pod.endpoint_url,
            chunks=chunks,
            voice_name=voice_name,
            workflow_run_id=workflow_run_id,
        )
    finally:
        try:
            await provider.terminate_pod(pod.id)
            log.info("tts pod terminated pod_id=%s", pod.id)
        except Exception as term_exc:
            log.error("failed to terminate tts pod %s: %s", pod.id, term_exc)

    log.info(
        "tts_synthesis complete chunks=%d total_dur=%.1fs pod=%s",
        len(chunk_results), total_duration_sec, pod.id,
    )

    return {
        "pod_id": pod.id,
        "chunk_count": len(chunk_results),
        "total_duration_sec": total_duration_sec,
        "chunks": chunk_results,
    }


async def _synthesize_all_chunks(
    endpoint_url: str,
    chunks: list[str],
    voice_name: str,
    workflow_run_id: str,
) -> tuple[list[dict[str, object]], float]:
    """Synthesize all chunks sequentially and persist each result to Postgres.

    Args:
        endpoint_url: Base URL of the ready TTS GPU pod.
        chunks: List of text chunks to synthesize.
        voice_name: Voice ID for the TTS endpoint.
        workflow_run_id: Hatchet workflow run ID (used for DB FK and logging).

    Returns:
        Tuple of (chunk_results, total_duration_sec).
    """
    # Parse workflow_id for DB FK (best-effort; None if run ID is not a UUID)
    workflow_id_uuid: uuid.UUID | None
    try:
        workflow_id_uuid = uuid.UUID(workflow_run_id)
    except ValueError:
        workflow_id_uuid = None
        log.warning(
            "workflow_run_id=%s is not a UUID; chunk rows will have no workflow FK",
            workflow_run_id,
        )

    chunk_results: list[dict[str, object]] = []
    total_duration_sec: float = 0.0

    async with httpx.AsyncClient(base_url=endpoint_url, timeout=_SYNTHESIZE_TIMEOUT_SEC) as client:
        for idx, chunk_text in enumerate(chunks):
            wav_bytes, attempts_used, duration_sec, validation_passed = (
                await _synthesize_with_retry(
                    client=client,
                    chunk_text=chunk_text,
                    chunk_index=idx,
                    voice_name=voice_name,
                    max_retries=_MAX_CHUNK_RETRIES,
                )
            )

            # Persist blob and update chunk progress row
            session_maker = _db.async_session_maker
            assert session_maker is not None, (
                "DB not initialized — call init_db() before starting the Hatchet worker"
            )
            async with session_maker() as session:
                blob = await blob_service.store(
                    session=session,
                    data=wav_bytes,
                    mime_type="audio/wav",
                    blob_type=BlobType.CHUNK_AUDIO,
                    workflow_id=workflow_id_uuid,
                )
                if workflow_id_uuid is not None:
                    svc = WorkflowService(session)
                    await svc.upsert_chunk(
                        workflow_id=workflow_id_uuid,
                        chunk_index=idx,
                        chunk_text=chunk_text,
                    )
                    if validation_passed:
                        await svc.complete_chunk_tts(
                            workflow_id=workflow_id_uuid,
                            chunk_index=idx,
                            blob_id=blob.id,
                            duration_sec=duration_sec,
                            attempts_used=attempts_used,
                        )
                    else:
                        # All attempts failed validation — save best-effort audio
                        # but mark the chunk failed so downstream steps can decide.
                        await svc.fail_chunk_tts(
                            workflow_id=workflow_id_uuid,
                            chunk_index=idx,
                            blob_id=blob.id,
                            attempts_used=attempts_used,
                        )
                await session.commit()

            total_duration_sec += duration_sec
            chunk_results.append({
                "index": idx,
                "text": chunk_text,
                "blob_id": str(blob.id),
                "duration_sec": duration_sec,
                "attempts_used": attempts_used,
                "validation_passed": validation_passed,
            })

            log.info(
                "chunk %d/%d done blob_id=%s dur=%.1fs attempts=%d validated=%s",
                idx + 1, len(chunks), blob.id, duration_sec,
                attempts_used, validation_passed,
            )

    return chunk_results, total_duration_sec


async def _synthesize_with_retry(
    client: httpx.AsyncClient,
    chunk_text: str,
    chunk_index: int,
    voice_name: str,
    max_retries: int,
) -> tuple[bytes, int, float, bool]:
    """Synthesize a single chunk, retrying on validation failure.

    Args:
        client: Configured httpx client pointing at the TTS pod.
        chunk_text: Text to synthesize.
        chunk_index: Zero-based chunk position (for logging).
        voice_name: Voice ID to pass to the TTS endpoint.
        max_retries: Maximum additional attempts after the first try.

    Returns:
        Tuple of (wav_bytes, attempts_used, duration_sec, validation_passed).
        If all attempts fail validation, returns best-effort audio with
        validation_passed=False so the caller can mark the chunk accordingly.
    """
    best_wav: bytes = b""
    best_duration: float = 0.0

    for attempt in range(max_retries + 1):
        seed = _BASE_SEED + attempt
        try:
            resp = await client.post(
                _SYNTHESIZE_PATH,
                json={"text": chunk_text, "voice": voice_name, "seed": seed},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.warning("chunk %d attempt %d HTTP error: %s", chunk_index, attempt + 1, exc)
            if attempt == max_retries:
                raise
            continue
        except httpx.RequestError as exc:
            log.warning("chunk %d attempt %d request error: %s", chunk_index, attempt + 1, exc)
            if attempt == max_retries:
                raise
            continue

        candidate = resp.content
        validation = validate_chunk_audio(candidate)
        best_wav = candidate
        best_duration = validation.duration_sec

        if validation.passed:
            log.debug(
                "chunk %d passed on attempt %d (dur=%.1fs)",
                chunk_index, attempt + 1, validation.duration_sec,
            )
            return candidate, attempt + 1, validation.duration_sec, True

        log.warning(
            "chunk %d validation failed attempt %d: %s",
            chunk_index, attempt + 1, validation.failure_reason,
        )

    # All attempts exhausted — return best-effort audio flagged as not validated
    log.error(
        "chunk %d: all %d attempt(s) failed validation; saving best-effort audio as FAILED",
        chunk_index, max_retries + 1,
    )
    return best_wav, max_retries + 1, best_duration, False
