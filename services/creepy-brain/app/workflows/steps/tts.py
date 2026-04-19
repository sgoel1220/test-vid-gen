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

import io
import logging
import uuid

import httpx
import numpy as np
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import StepContext

from app.audio.encoding import encode_wav_to_mp3
from app.audio.validation import validate_chunk_audio
from app.config import settings
from app.gpu import GpuPodSpec
from app.gpu.lifecycle import workflow_gpu_pod
from app.models.enums import BlobType, ChunkStatus
from app.models.json_schemas import GenerateStoryStepOutput, WorkflowInputSchema
from app.services import blob_service
from app.services import story_service as _story_service
from app.services.workflow_service import (
    WorkflowService,
    get_chunks_for_image_step,
    get_optional_workflow_id,
)
from app.text.chunking import chunk_text_by_sentences
from app.text.normalization import normalize_text
from app.workflows.db_helpers import get_session_maker

log = logging.getLogger(__name__)

_SYNTHESIZE_PATH = "/synthesize"

# Synthesis parameters (timeouts and retries only - TTS params come from config)
_MAX_CHUNK_RETRIES = 2  # up to 3 total attempts per chunk
_MAX_REQUEUE_ROUNDS = 2  # additional retry rounds for failed chunks
_SYNTHESIZE_TIMEOUT_SEC = 120


class TtsChunkResult(BaseModel):
    """Result of synthesizing a single text chunk."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Zero-based chunk position")
    text: str = Field(description="Original chunk text")
    blob_id: str = Field(description="UUID of the saved WAV blob (empty on failure)")
    duration_sec: float = Field(ge=0, description="Audio duration in seconds")
    attempts_used: int = Field(ge=1, description="Number of synthesis attempts")
    validation_passed: bool = Field(description="Whether chunk passed audio validation")


class TtsStepOutput(BaseModel):
    """Output of the tts_synthesis step."""

    model_config = ConfigDict(extra="forbid")

    pod_id: str = Field(description="GPU pod ID used for synthesis")
    chunk_count: int = Field(ge=0, description="Number of chunks synthesized")
    total_duration_sec: float = Field(ge=0, description="Total audio duration")
    chunks: list[TtsChunkResult] = Field(description="Per-chunk results")


class TtsAllChunksResult(BaseModel):
    """Aggregate result after synthesizing all queued chunks."""

    model_config = ConfigDict(extra="forbid")

    chunk_results: list[TtsChunkResult] = Field(description="Per-chunk synthesis results")
    total_duration_sec: float = Field(ge=0, description="Total valid audio duration")


class ChunkSynthesisResult(BaseModel):
    """Result of a single chunk synthesis attempt group."""

    model_config = ConfigDict(extra="forbid")

    wav_bytes: bytes = Field(description="Best available WAV bytes")
    attempts_used: int = Field(ge=1, description="Number of attempts made")
    duration_sec: float = Field(ge=0, description="Best available duration")
    validation_passed: bool = Field(description="Whether validation passed")


async def execute(input: WorkflowInputSchema, ctx: StepContext) -> TtsStepOutput:
    """Synthesize audio for the story via a TTS GPU pod.

    Supports sub-unit resume: if some chunks are already COMPLETED in DB
    (from a previous partial run), only pending chunks are synthesized.
    No GPU pod is spun up if all chunks are already done.

    Args:
        input: Validated workflow input (contains voice_name).
        ctx: step execution context (provides workflow_run_id and parent outputs).

    Returns:
        Pydantic output model with pod_id, chunk_count, total_duration_sec, and chunks.
    """
    # --- 1. Get story_id from parent step output, then fetch full_text from DB ---
    story_result = ctx.get_parent_output("generate_story", GenerateStoryStepOutput)
    if story_result is None:
        raise ValueError("generate_story step did not produce story_id")

    story_id_for_text: uuid.UUID = story_result.story_id
    story_id_str: str = str(story_id_for_text)

    session_maker = get_session_maker()
    async with session_maker() as _session:
        _story = await _story_service.get(_session, story_id_for_text)

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
        workflow_run_id,
        story_id_str,
        voice_name,
        len(full_text),
    )

    # --- 2. Normalize full text (LLM call, cached in-process by text hash) ---
    normalized_text = await normalize_text(full_text)

    # --- 3. Chunk normalized text ---
    chunks: list[str] = chunk_text_by_sentences(normalized_text, chunk_size=settings.tts_chunk_size)
    if not chunks:
        raise ValueError("text chunking produced zero chunks")

    log.info("tts_synthesis: %d chunks to synthesize", len(chunks))

    # --- 3.5. Persist all chunks to DB (text only, PENDING status) ---
    workflow_id_uuid = get_optional_workflow_id(workflow_run_id)
    if workflow_id_uuid is not None:
        async with session_maker() as session:
            svc = WorkflowService(session)
            for idx, chunk_text in enumerate(chunks):
                await svc.upsert_chunk(
                    workflow_id=workflow_id_uuid,
                    chunk_index=idx,
                    chunk_text=chunk_text,
                )
            await session.commit()
        log.info("tts_synthesis: persisted %d chunks to DB", len(chunks))

    # --- 4. Read DB chunks back and split by status (DB is the source of truth) ---
    # pending_chunks uses DB text so what is synthesized == what is stored.
    resumed_results: list[TtsChunkResult] = []
    pending_chunks: list[tuple[int, str]] = []
    resumed_duration: float = 0.0

    if workflow_id_uuid is not None:
        async with session_maker() as session:
            db_chunks = await get_chunks_for_image_step(session, workflow_id_uuid)

        for db_chunk in db_chunks:
            if db_chunk.tts_status == ChunkStatus.COMPLETED and db_chunk.blob_id:
                dur = db_chunk.duration_sec or 0.0
                resumed_results.append(TtsChunkResult(
                    index=db_chunk.index,
                    text=db_chunk.text,
                    blob_id=db_chunk.blob_id,
                    duration_sec=dur,
                    attempts_used=1,
                    validation_passed=True,
                ))
                resumed_duration += dur
            else:
                pending_chunks.append((db_chunk.index, db_chunk.text))
    else:
        pending_chunks = list(enumerate(chunks))

    if resumed_results:
        log.info(
            "tts_synthesis: resuming — %d chunks already done, %d pending",
            len(resumed_results),
            len(pending_chunks),
        )

    # --- 5. If no pending chunks, return early (no GPU pod needed) ---
    if not pending_chunks:
        log.info("tts_synthesis: all %d chunks already completed, skipping GPU pod", len(chunks))
        all_results = sorted(resumed_results, key=lambda r: r.index)
        return TtsStepOutput(
            pod_id="",
            chunk_count=len(all_results),
            total_duration_sec=resumed_duration,
            chunks=all_results,
        )

    # --- 6-7. Spin up TTS GPU pod, wait for ready, synthesize, terminate ---
    workflow_id_for_pod = get_optional_workflow_id(workflow_run_id)
    async with workflow_gpu_pod(
        session_maker,
        spec=GpuPodSpec.from_config(),
        idempotency_key=f"tts-{workflow_run_id}",
        workflow_id=workflow_id_for_pod,
        label="tts",
        service_port=settings.gpu_port,
    ) as (pod, endpoint_url):
        all_chunks_result = await _synthesize_all_chunks(
            endpoint_url=endpoint_url,
            pending_chunks=pending_chunks,
            total_chunk_count=len(chunks),
            voice_name=voice_name,
            workflow_run_id=workflow_run_id,
            session_maker=session_maker,
        )

    # --- 8. Merge resumed + newly synthesized results ---
    merged = resumed_results + all_chunks_result.chunk_results
    merged.sort(key=lambda r: r.index)
    total_duration = resumed_duration + all_chunks_result.total_duration_sec

    log.info(
        "tts_synthesis complete chunks=%d total_dur=%.1fs pod=%s",
        len(merged), total_duration, pod.id,
    )

    return TtsStepOutput(
        pod_id=pod.id,
        chunk_count=len(merged),
        total_duration_sec=total_duration,
        chunks=merged,
    )


async def _synthesize_all_chunks(
    endpoint_url: str,
    pending_chunks: list[tuple[int, str]],
    total_chunk_count: int,
    voice_name: str,
    workflow_run_id: str,
    session_maker: async_sessionmaker[AsyncSession],
) -> TtsAllChunksResult:
    """Synthesize pending chunks sequentially and persist each result to Postgres.

    Failed chunks are re-queued for additional retry rounds (up to
    ``_MAX_REQUEUE_ROUNDS``) with shifted seeds so each round produces
    different audio.

    Args:
        endpoint_url: Base URL of the ready TTS GPU pod.
        pending_chunks: List of (chunk_index, chunk_text) tuples to synthesize.
        total_chunk_count: Total number of chunks in the workflow (for logging).
        voice_name: Voice ID for the TTS endpoint.
        workflow_run_id: workflow run ID (used for DB FK and logging).
        session_maker: SQLAlchemy async session factory.

    Returns:
        Pydantic model with chunk results and total duration.
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

    chunk_results: list[TtsChunkResult] = []
    total_duration_sec: float = 0.0

    # Use the provided pending list directly (already filtered for resume)
    pending: list[tuple[int, str]] = list(pending_chunks)

    async with httpx.AsyncClient(base_url=endpoint_url, timeout=_SYNTHESIZE_TIMEOUT_SEC) as client:
        attempts_per_chunk = _MAX_CHUNK_RETRIES + 1  # attempts used per round

        for requeue_round in range(_MAX_REQUEUE_ROUNDS + 1):
            if not pending:
                break

            if requeue_round > 0:
                log.info(
                    "requeue round %d: retrying %d failed chunk(s)",
                    requeue_round,
                    len(pending),
                )

            # Seed offset ensures each round uses fresh seeds
            seed_offset: int = requeue_round * attempts_per_chunk
            failed: list[tuple[int, str]] = []

            for idx, chunk_text in pending:
                synthesis_result = await _synthesize_with_retry(
                    client=client,
                    chunk_text=chunk_text,
                    chunk_index=idx,
                    voice_name=voice_name,
                    max_retries=_MAX_CHUNK_RETRIES,
                    seed_offset=seed_offset,
                )

                # Encode WAV → MP3 (best-effort; never blocks saving the WAV)
                mp3_bytes: bytes | None = None
                try:
                    audio_arr: np.ndarray[tuple[int, ...], np.dtype[np.float32]]
                    audio_arr, sr = sf.read(
                        io.BytesIO(synthesis_result.wav_bytes), dtype="float32"
                    )
                    mp3_bytes = await encode_wav_to_mp3(audio_arr, sr)
                except Exception as mp3_err:
                    log.warning("chunk %d: MP3 encoding failed: %s", idx, mp3_err)

                # Persist blob(s) and update chunk progress row
                async with session_maker() as session:
                    blob = await blob_service.store(
                        session=session,
                        data=synthesis_result.wav_bytes,
                        mime_type="audio/wav",
                        blob_type=BlobType.CHUNK_AUDIO,
                        workflow_id=workflow_id_uuid,
                    )
                    mp3_blob_id: uuid.UUID | None = None
                    if mp3_bytes is not None:
                        mp3_blob = await blob_service.store(
                            session=session,
                            data=mp3_bytes,
                            mime_type="audio/mpeg",
                            blob_type=BlobType.CHUNK_AUDIO_MP3,
                            workflow_id=workflow_id_uuid,
                        )
                        mp3_blob_id = mp3_blob.id
                    if workflow_id_uuid is not None:
                        svc = WorkflowService(session)
                        if synthesis_result.validation_passed:
                            await svc.complete_chunk_tts(
                                workflow_id=workflow_id_uuid,
                                chunk_index=idx,
                                blob_id=blob.id,
                                duration_sec=synthesis_result.duration_sec,
                                attempts_used=synthesis_result.attempts_used,
                                mp3_blob_id=mp3_blob_id,
                            )
                        else:
                            # Save best-effort audio but mark failed for now;
                            # will be overwritten if a later requeue round succeeds.
                            await svc.fail_chunk_tts(
                                workflow_id=workflow_id_uuid,
                                chunk_index=idx,
                                blob_id=blob.id,
                                attempts_used=synthesis_result.attempts_used,
                            )
                    await session.commit()

                total_duration_sec += synthesis_result.duration_sec

                if synthesis_result.validation_passed:
                    chunk_results.append(TtsChunkResult(
                        index=idx,
                        text=chunk_text,
                        blob_id=str(blob.id),
                        duration_sec=synthesis_result.duration_sec,
                        attempts_used=synthesis_result.attempts_used,
                        validation_passed=True,
                    ))
                    log.info(
                        "chunk %d/%d done blob_id=%s dur=%.1fs attempts=%d validated=True",
                        idx + 1,
                        total_chunk_count,
                        blob.id,
                        synthesis_result.duration_sec,
                        synthesis_result.attempts_used,
                    )
                else:
                    # Enqueue for next round (or record final failure below)
                    failed.append((idx, chunk_text))
                    log.warning(
                        "chunk %d/%d failed round %d — will %s",
                        idx + 1,
                        total_chunk_count,
                        requeue_round,
                        "requeue" if requeue_round < _MAX_REQUEUE_ROUNDS else "save as FAILED",
                    )

            pending = failed

        # Any chunks still in pending after all rounds are final failures
        for idx, chunk_text in pending:
            chunk_results.append(
                TtsChunkResult(
                    index=idx,
                    text=chunk_text,
                    blob_id="",  # last blob was already persisted in the loop
                    duration_sec=0.0,
                    attempts_used=(_MAX_CHUNK_RETRIES + 1) * (_MAX_REQUEUE_ROUNDS + 1),
                    validation_passed=False,
                )
            )
            log.error(
                "chunk %d/%d: exhausted all %d requeue round(s); marked FAILED",
                idx + 1,
                total_chunk_count,
                _MAX_REQUEUE_ROUNDS + 1,
            )

    return TtsAllChunksResult(
        chunk_results=chunk_results,
        total_duration_sec=total_duration_sec,
    )


async def _synthesize_with_retry(
    client: httpx.AsyncClient,
    chunk_text: str,
    chunk_index: int,
    voice_name: str,
    max_retries: int,
    seed_offset: int = 0,
) -> ChunkSynthesisResult:
    """Synthesize a single chunk, retrying on validation failure.

    Args:
        client: Configured httpx client pointing at the TTS pod.
        chunk_text: Text to synthesize.
        chunk_index: Zero-based chunk position (for logging).
        voice_name: Voice ID to pass to the TTS endpoint.
        max_retries: Maximum additional attempts after the first try.

    Returns:
        Pydantic model with WAV bytes, attempts, duration, and validation status.
        If all attempts fail validation, returns best-effort audio with
        validation_passed=False so the caller can mark the chunk accordingly.
    """
    best_wav: bytes = b""
    best_duration: float = 0.0

    for attempt in range(max_retries + 1):
        # Increment seed on retry so we get different audio each attempt.
        # seed_offset shifts the range for re-queued chunks so they don't
        # repeat seeds from earlier rounds.
        seed = settings.tts_seed + seed_offset + attempt
        try:
            resp = await client.post(
                _SYNTHESIZE_PATH,
                json={
                    "text": chunk_text,
                    "voice": voice_name,
                    "seed": seed,
                    "exaggeration": settings.tts_exaggeration,
                    "cfg_weight": settings.tts_cfg_weight,
                    "temperature": settings.tts_temperature,
                    "repetition_penalty": settings.tts_repetition_penalty,
                    "min_p": settings.tts_min_p,
                    "top_p": settings.tts_top_p,
                },
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
                chunk_index,
                attempt + 1,
                validation.duration_sec,
            )
            return ChunkSynthesisResult(
                wav_bytes=candidate,
                attempts_used=attempt + 1,
                duration_sec=validation.duration_sec,
                validation_passed=True,
            )

        log.warning(
            "chunk %d validation failed attempt %d: %s",
            chunk_index,
            attempt + 1,
            validation.failure_reason,
        )

    # All attempts exhausted — return best-effort audio flagged as not validated
    log.error(
        "chunk %d: all %d attempt(s) failed validation; saving best-effort audio as FAILED",
        chunk_index,
        max_retries + 1,
    )
    return ChunkSynthesisResult(
        wav_bytes=best_wav,
        attempts_used=max_retries + 1,
        duration_sec=best_duration,
        validation_passed=False,
    )
