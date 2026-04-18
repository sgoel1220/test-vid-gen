"""Run orchestration: settings resolution, chunk synthesis, artifact saving."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

import engine
from audio import apply_speed_factor, encode_audio, stitch_audio_chunks, post_process_final_audio
from audio.processing import validate_chunk_audio  # type: ignore[attr-defined]
from config import (
    get_audio_sample_rate,
    get_gen_default_cfg_weight,
    get_gen_default_exaggeration,
    get_gen_default_language,
    get_gen_default_seed,
    get_gen_default_speed_factor,
    get_gen_default_temperature,
    get_output_path,
    get_reference_audio_path,
)
from enums import AudioFormat, JobStatus
from files import validate_reference_audio
from job_store import job_store
from models import (
    ChunkPreviewInfo,
    ChunkValidationResult,
    LiteCloneRunResponse,
    LiteCloneTTSRequest,
    ResolvedSettings,
    SavedAudioArtifact,
    SavedChunkInfo,
)
from text import chunk_text_by_sentences, normalize_text_with_llm, sanitize_filename


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

_MIME_TYPE: dict[AudioFormat, str] = {
    AudioFormat.WAV: "audio/wav",
    AudioFormat.MP3: "audio/mpeg",
    AudioFormat.OPUS: "audio/ogg; codecs=opus",
}


# ---------------------------------------------------------------------------
# Settings resolution
# ---------------------------------------------------------------------------

def resolve_lite_clone_settings(request: LiteCloneTTSRequest) -> ResolvedSettings:
    return ResolvedSettings(
        reference_audio_filename=request.reference_audio_filename,
        output_format=request.output_format,
        target_sample_rate=request.target_sample_rate or get_audio_sample_rate(),
        split_text=request.split_text,
        chunk_size=request.chunk_size,
        temperature=request.temperature if request.temperature is not None else get_gen_default_temperature(),
        exaggeration=request.exaggeration if request.exaggeration is not None else get_gen_default_exaggeration(),
        cfg_weight=request.cfg_weight if request.cfg_weight is not None else get_gen_default_cfg_weight(),
        seed=request.seed if request.seed is not None else get_gen_default_seed(),
        speed_factor=request.speed_factor if request.speed_factor is not None else get_gen_default_speed_factor(),
        language=request.language if request.language is not None else get_gen_default_language(),
        enable_smart_stitching=request.enable_smart_stitching,
        sentence_pause_ms=request.sentence_pause_ms,
        crossfade_ms=request.crossfade_ms,
        safety_fade_ms=request.safety_fade_ms,
        enable_dc_removal=request.enable_dc_removal,
        dc_highpass_hz=request.dc_highpass_hz,
        peak_normalize_threshold=request.peak_normalize_threshold,
        peak_normalize_target=request.peak_normalize_target,
        enable_silence_trimming=request.enable_silence_trimming,
        enable_internal_silence_fix=request.enable_internal_silence_fix,
        enable_unvoiced_removal=request.enable_unvoiced_removal,
        max_reference_duration_sec=request.max_reference_duration_sec,
        save_chunk_audio=request.save_chunk_audio,
        save_final_audio=request.save_final_audio,
        run_label=request.run_label,
        enable_chunk_validation=request.enable_chunk_validation,
        max_chunk_retries=request.max_chunk_retries,
        chunk_validation_min_rms=request.chunk_validation_min_rms,
        chunk_validation_min_peak=request.chunk_validation_min_peak,
        chunk_validation_min_voiced_ratio=request.chunk_validation_min_voiced_ratio,
        enable_text_normalization=request.enable_text_normalization,
        text_normalization_model_id=request.text_normalization_model_id,
    )


# ---------------------------------------------------------------------------
# Chunk / preview helpers
# ---------------------------------------------------------------------------

def build_text_chunks(text: str, split_text: bool, chunk_size: int) -> List[str]:
    if split_text and len(text) > chunk_size * 1.5:
        return chunk_text_by_sentences(text, chunk_size)
    return [text]


def build_chunk_preview(text: str, split_text: bool, chunk_size: int) -> List[ChunkPreviewInfo]:
    return [
        ChunkPreviewInfo(index=i, text=chunk, char_count=len(chunk))
        for i, chunk in enumerate(build_text_chunks(text, split_text, chunk_size), start=1)
    ]


def _normalize_chunk_indices(indices: List[int], total: int) -> List[int]:
    if total <= 0:
        raise HTTPException(status_code=400, detail="No text chunks were produced.")
    if not indices:
        return list(range(1, total + 1))
    seen: set[int] = set()
    result: list[int] = []
    for idx in indices:
        if idx < 1 or idx > total:
            raise HTTPException(
                status_code=400,
                detail=f"Chunk index {idx} out of range for {total} available chunks.",
            )
        if idx not in seen:
            result.append(idx)
            seen.add(idx)
    return result


def build_selected_chunk_entries(
    preview: List[ChunkPreviewInfo], selected: List[int]
) -> List[Tuple[int, str]]:
    by_index = {c.index: c for c in preview}
    indices = _normalize_chunk_indices(selected, len(preview))
    return [(i, by_index[i].text) for i in indices]


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

def _artifact_url(relative_path: Path) -> str:
    return f"/outputs/{relative_path.as_posix()}"


def _duration_sec(audio: np.ndarray, sample_rate: int) -> float:
    return round(float(len(audio)) / float(sample_rate), 3) if sample_rate > 0 else 0.0


def save_audio_artifact(
    output_root: Path,
    file_path: Path,
    audio_array: np.ndarray,
    sample_rate: int,
    output_format: AudioFormat,
    target_sample_rate: int,
) -> SavedAudioArtifact:
    encoded = encode_audio(audio_array, sample_rate, output_format, target_sample_rate)
    if not encoded or len(encoded) < 100:
        raise ValueError(f"Failed to encode audio for {file_path.name}")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(encoded)
    rel = file_path.relative_to(output_root)
    return SavedAudioArtifact(
        filename=file_path.name,
        relative_path=rel.as_posix(),
        url=_artifact_url(rel),
        format=output_format,
        sample_rate=sample_rate,
        target_sample_rate=target_sample_rate,
        byte_size=len(encoded),
        duration_sec=_duration_sec(audio_array, sample_rate),
    )


def _build_final_audio_filename(ref_filename: str, settings: ResolvedSettings) -> str:
    label = sanitize_filename(settings.run_label or "stable")
    ref_tag = sanitize_filename(Path(ref_filename).stem)

    def fmt(v: float) -> str:
        return f"{float(v):g}"

    return (
        f"{label}__{ref_tag}__T{fmt(settings.temperature)}"
        f"_E{fmt(settings.exaggeration)}_CFG{fmt(settings.cfg_weight)}"
        f"_seed{settings.seed}.{settings.output_format.value}"
    )


def _build_run_dir_name(
    ref_filename: str,
    run_label: Optional[str] = None,
    timestamp: Optional[str] = None,
    suffix: Optional[str] = None,
) -> str:
    timestamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
    suffix = suffix or uuid.uuid4().hex[:8]
    ref_tag = sanitize_filename(Path(ref_filename).stem)
    parts = [timestamp]
    if run_label:
        label = sanitize_filename(run_label)
        if label:
            parts.append(label)
    parts += [ref_tag, suffix]
    return "__".join(parts)


def _resolve_reference_path(ref_filename: str, max_duration: Optional[int]) -> Path:
    path = get_reference_audio_path(ensure_absolute=True) / ref_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Reference audio '{ref_filename}' not found.")
    ok, msg = validate_reference_audio(path, max_duration)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return path


def _write_manifest(output_dir: Path, response: LiteCloneRunResponse) -> Path:
    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps(jsonable_encoder(response), indent=2), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Core run execution
# ---------------------------------------------------------------------------

async def execute_lite_clone_run(
    request: LiteCloneTTSRequest,
    progress_callback: Optional[Callable[..., None]] = None,
) -> LiteCloneRunResponse:
    if not engine.is_model_ready():
        raise HTTPException(status_code=503, detail="TTS model is not loaded.")

    settings = resolve_lite_clone_settings(request)
    ref_path = _resolve_reference_path(
        request.reference_audio_filename, settings.max_reference_duration_sec
    )

    synthesis_text = request.text
    normalized_text: Optional[str] = None
    if settings.enable_text_normalization:
        norm_cache = get_output_path(ensure_absolute=True) / "text_norm_cache"
        norm_cache.mkdir(parents=True, exist_ok=True)
        normalized, _ = await asyncio.to_thread(
            normalize_text_with_llm,
            synthesis_text,
            model_id=settings.text_normalization_model_id,
            cache_dir=norm_cache,
        )
        if normalized != synthesis_text:
            normalized_text = normalized
            synthesis_text = normalized

    preview = build_chunk_preview(synthesis_text, settings.split_text, settings.chunk_size)
    if not preview:
        raise HTTPException(status_code=400, detail="No text chunks were produced.")

    selected_entries = build_selected_chunk_entries(preview, request.selected_chunk_indices)
    selected_indices = [i for i, _ in selected_entries]
    total_steps = len(selected_entries) + 2
    outputs_root = get_output_path(ensure_absolute=True)

    if progress_callback:
        progress_callback(
            message=f"Prepared {len(selected_entries)} chunk(s) from {len(preview)} available.",
            progress_completed=0,
            progress_total=total_steps,
            current_chunk_index=None,
            selected_chunk_indices=selected_indices,
        )

    run_id = _build_run_dir_name(request.reference_audio_filename, request.run_label)
    run_root = outputs_root / "lite_clone_runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    run_warnings: List[str] = []
    chunk_records: List[SavedChunkInfo] = []
    raw_chunks: List[np.ndarray] = []
    engine_sr: Optional[int] = None

    try:
        for done_count, (chunk_idx, chunk_text) in enumerate(selected_entries, start=1):
            logger.info("Synthesising chunk %d/%d.", chunk_idx, len(preview))
            if progress_callback:
                progress_callback(
                    message=f"Synthesising chunk {chunk_idx} ({done_count}/{len(selected_entries)})",
                    progress_completed=done_count - 1,
                    progress_total=total_steps,
                    current_chunk_index=chunk_idx,
                    selected_chunk_indices=selected_indices,
                )

            base_seed = settings.seed
            max_attempts = (1 + settings.max_chunk_retries) if settings.enable_chunk_validation else 1
            best_audio: Optional[np.ndarray] = None
            best_sr: Optional[int] = None
            validation_result: Optional[ChunkValidationResult] = None
            attempts_used = 0

            for attempt in range(max_attempts):
                attempt_seed = base_seed + attempt if attempt > 0 else base_seed
                tensor, chunk_sr = await asyncio.to_thread(
                    engine.synthesize,
                    text=chunk_text,
                    audio_prompt_path=str(ref_path),
                    temperature=settings.temperature,
                    exaggeration=settings.exaggeration,
                    cfg_weight=settings.cfg_weight,
                    seed=attempt_seed,
                    language=settings.language,
                )
                if tensor is None or chunk_sr is None:
                    raise HTTPException(status_code=500, detail=f"TTS engine failed on chunk {chunk_idx}.")

                if settings.speed_factor != 1.0:
                    tensor, _ = await asyncio.to_thread(
                        apply_speed_factor, tensor, chunk_sr, settings.speed_factor
                    )

                attempt_audio = tensor.cpu().numpy().squeeze().astype(np.float32)
                attempts_used = attempt + 1

                if not settings.enable_chunk_validation:
                    best_audio = attempt_audio
                    best_sr = chunk_sr
                    break

                v = await asyncio.to_thread(
                    validate_chunk_audio,
                    attempt_audio,
                    chunk_sr,
                    min_rms_energy=settings.chunk_validation_min_rms,
                    min_peak_amplitude=settings.chunk_validation_min_peak,
                    min_voiced_ratio=settings.chunk_validation_min_voiced_ratio,
                )
                validation_result = v
                if best_audio is None:
                    best_audio, best_sr = attempt_audio, chunk_sr
                if v.passed:
                    best_audio, best_sr = attempt_audio, chunk_sr
                    break
                logger.warning("Chunk %d attempt %d failed: %s", chunk_idx, attempt + 1, v.failures)

            if best_audio is None or best_sr is None:
                raise HTTPException(status_code=500, detail=f"TTS engine failed on chunk {chunk_idx}.")

            if settings.enable_chunk_validation and validation_result and not validation_result.passed:
                run_warnings.append(
                    f"Chunk {chunk_idx}: all {attempts_used} attempt(s) failed validation "
                    f"({validation_result.failures}); using best-effort audio."
                )

            chunk_sr = best_sr
            if engine_sr is None:
                engine_sr = chunk_sr
            elif engine_sr != chunk_sr:
                run_warnings.append(f"Chunk {chunk_idx} returned SR {chunk_sr}, keeping {engine_sr}.")

            raw_chunks.append(best_audio)

            artifact = None
            if settings.save_chunk_audio:
                chunk_file = run_root / f"chunk_{chunk_idx:03d}.{settings.output_format.value}"
                artifact = save_audio_artifact(
                    outputs_root, chunk_file, best_audio, chunk_sr,
                    settings.output_format, settings.target_sample_rate,
                )

            chunk_records.append(SavedChunkInfo(
                index=chunk_idx, text=chunk_text, artifact=artifact,
                validation=validation_result, attempts_used=attempts_used,
            ))

            if progress_callback:
                progress_callback(
                    message=f"Finished chunk {chunk_idx} ({done_count}/{len(selected_entries)})",
                    progress_completed=done_count,
                    progress_total=total_steps,
                    current_chunk_index=chunk_idx,
                    selected_chunk_indices=selected_indices,
                )

        if engine_sr is None:
            raise HTTPException(status_code=500, detail="Engine sample rate could not be determined.")

        if progress_callback:
            progress_callback(
                message="Stitching audio chunks",
                progress_completed=len(selected_entries),
                progress_total=total_steps,
                current_chunk_index=None,
                selected_chunk_indices=selected_indices,
            )

        stitched = await asyncio.to_thread(stitch_audio_chunks, raw_chunks, engine_sr, settings)
        final_audio = await asyncio.to_thread(
            post_process_final_audio, stitched, engine_sr, settings, run_warnings
        )

        if progress_callback:
            progress_callback(
                message="Finalising saved output",
                progress_completed=len(selected_entries) + 1,
                progress_total=total_steps,
                current_chunk_index=None,
                selected_chunk_indices=selected_indices,
            )

        final_artifact = None
        if settings.save_final_audio:
            final_artifact = save_audio_artifact(
                outputs_root,
                run_root / _build_final_audio_filename(request.reference_audio_filename, settings),
                final_audio, engine_sr, settings.output_format, settings.target_sample_rate,
            )

        manifest_rel = (Path("lite_clone_runs") / run_id / "manifest.json")
        response = LiteCloneRunResponse(
            run_id=run_id,
            output_dir=(Path("lite_clone_runs") / run_id).as_posix(),
            reference_audio_filename=request.reference_audio_filename,
            source_chunk_count=len(preview),
            chunk_count=len(chunk_records),
            selected_chunk_indices=selected_indices,
            resolved_settings=settings,
            chunks=chunk_records,
            final_audio=final_artifact,
            manifest_relative_path=manifest_rel.as_posix(),
            manifest_url=_artifact_url(manifest_rel),
            warnings=run_warnings,
            normalized_text=normalized_text,
        )

        _write_manifest(run_root, response)

        if progress_callback:
            progress_callback(
                message=f"Saved run {run_id}",
                progress_completed=total_steps,
                progress_total=total_steps,
                current_chunk_index=None,
                selected_chunk_indices=selected_indices,
            )

        return response

    except BaseException:
        raise


# ---------------------------------------------------------------------------
# Background job worker
# ---------------------------------------------------------------------------

async def run_lite_clone_job(job_id: str, request: LiteCloneTTSRequest) -> None:
    try:
        job_store.update(job_id, status=JobStatus.RUNNING, message="Preparing run")
        result = await execute_lite_clone_run(
            request,
            progress_callback=lambda **kw: job_store.update(
                job_id, status=JobStatus.RUNNING, error=None, **kw
            ),
        )
        job_store.update(
            job_id,
            status=JobStatus.COMPLETED,
            message=f"Saved run {result.run_id}",
            result=result,
            error=None,
            current_chunk_index=None,
        )
    except HTTPException as exc:
        msg = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
        job_store.update(job_id, status=JobStatus.FAILED, message=msg, error=msg, current_chunk_index=None)
    except Exception as exc:
        logger.exception("Lite clone job %s failed.", job_id)
        msg = str(exc) or "Generation failed."
        job_store.update(job_id, status=JobStatus.FAILED, message=msg, error=msg, current_chunk_index=None)
