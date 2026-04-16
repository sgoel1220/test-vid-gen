"""Converters between ORM models and wire DTOs.

This is the ONLY module where ORM models and protocol DTOs are allowed to touch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from creepy_pasta_protocol.audio import AudioBlobDTO
from creepy_pasta_protocol.chunks import ChunkDTO
from creepy_pasta_protocol.common import RunStatus
from creepy_pasta_protocol.runs import (
    CreateRunRequest,
    PatchRunRequest,
    RunDetailDTO,
    RunSummaryDTO,
    RunWarnings,
)
from creepy_pasta_protocol.scripts import ScriptDTO
from creepy_pasta_protocol.stories import (
    PatchStoryRequest,
    StoryActDTO,
    StoryDetailDTO,
    StoryStatus,
    StorySummaryDTO,
)
from creepy_pasta_protocol.voices import VoiceDTO

from app.models import AudioBlob, Chunk, Run, Script, Story, StoryAct, Voice


def script_to_dto(orm: Script) -> ScriptDTO:
    return ScriptDTO(
        id=str(orm.id),
        text=orm.text,
        text_sha256=orm.text_sha256,
        char_count=orm.char_count,
        created_at=orm.created_at,
    )


def voice_to_dto(orm: Voice) -> VoiceDTO:
    return VoiceDTO(
        id=str(orm.id),
        filename=orm.filename,
        audio_blob_id=str(orm.audio_blob_id),
        duration_sec=float(orm.duration_sec),
        created_at=orm.created_at,
    )


def audio_blob_to_dto(orm: AudioBlob) -> AudioBlobDTO:
    return AudioBlobDTO(
        id=str(orm.id),
        storage_backend=orm.storage_backend,
        storage_key=orm.storage_key,
        sha256=orm.sha256,
        byte_size=orm.byte_size,
        mime_type=orm.mime_type,
        format=orm.format,
        sample_rate=orm.sample_rate,
        duration_sec=float(orm.duration_sec),
        created_at=orm.created_at,
    )


def chunk_to_dto(orm: Chunk) -> ChunkDTO:
    return ChunkDTO(
        id=str(orm.id),
        run_id=str(orm.run_id),
        chunk_index=orm.chunk_index,
        text=orm.text,
        audio_blob_id=str(orm.audio_blob_id) if orm.audio_blob_id is not None else None,
        attempts_used=orm.attempts_used,
        validation=orm.validation,
    )


def run_to_summary(orm: Run) -> RunSummaryDTO:
    return RunSummaryDTO(
        id=str(orm.id),
        script_id=str(orm.script_id),
        voice_id=str(orm.voice_id) if orm.voice_id is not None else None,
        run_label=orm.run_label,
        status=orm.status,
        output_format=orm.output_format,
        source_chunk_count=orm.source_chunk_count,
        selected_chunk_indices=list(orm.selected_chunk_indices),
        error=orm.error,
        created_at=orm.created_at,
        started_at=orm.started_at,
        completed_at=orm.completed_at,
        pod_run_id=orm.pod_run_id,
    )


def run_to_detail(orm: Run) -> RunDetailDTO:
    return RunDetailDTO(
        id=str(orm.id),
        script_id=str(orm.script_id),
        voice_id=str(orm.voice_id) if orm.voice_id is not None else None,
        run_label=orm.run_label,
        status=orm.status,
        output_format=orm.output_format,
        source_chunk_count=orm.source_chunk_count,
        selected_chunk_indices=list(orm.selected_chunk_indices),
        error=orm.error,
        created_at=orm.created_at,
        started_at=orm.started_at,
        completed_at=orm.completed_at,
        pod_run_id=orm.pod_run_id,
        settings=orm.settings,
        normalized_text=orm.normalized_text,
        warnings=orm.warnings.warnings if orm.warnings is not None else [],
        final_audio_id=str(orm.final_audio_id) if orm.final_audio_id is not None else None,
        chunks=[chunk_to_dto(c) for c in orm.chunks],
    )


def create_run_request_to_orm_kwargs(
    req: CreateRunRequest,
    resolved_script_id: uuid.UUID,
) -> dict[str, Any]:  # Any: sole permitted use in this codebase
    return {
        "id": uuid.uuid4(),
        "script_id": resolved_script_id,
        "voice_id": uuid.UUID(req.voice_id) if req.voice_id is not None else None,
        "run_label": req.run_label,
        "status": RunStatus.PENDING,
        "settings": req.settings,
        "output_format": req.output_format,
        "source_chunk_count": req.source_chunk_count,
        "selected_chunk_indices": list(req.selected_chunk_indices),
        "normalized_text": req.normalized_text,
        "pod_run_id": req.pod_run_id,
        "created_at": datetime.now(timezone.utc),
    }


def patch_run_request_apply(orm: Run, req: PatchRunRequest) -> None:
    """Mutate *orm* with only the fields explicitly set in *req*."""
    fields = req.model_fields_set
    if "status" in fields and req.status is not None:
        orm.status = req.status
    if "error" in fields:
        orm.error = req.error
    if "final_audio_id" in fields:
        orm.final_audio_id = (
            uuid.UUID(req.final_audio_id) if req.final_audio_id is not None else None
        )
    if "warnings" in fields:
        orm.warnings = (
            RunWarnings(warnings=req.warnings) if req.warnings is not None else None
        )
    if "started_at" in fields:
        orm.started_at = req.started_at
    if "completed_at" in fields:
        orm.completed_at = req.completed_at


# ---------------------------------------------------------------------------
# Story converters
# ---------------------------------------------------------------------------


def story_act_to_dto(orm: StoryAct) -> StoryActDTO:
    return StoryActDTO(
        id=str(orm.id),
        story_id=str(orm.story_id),
        act_number=orm.act_number,
        title=orm.title,
        target_word_count=orm.target_word_count,
        text=orm.text,
        word_count=orm.word_count,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def story_to_summary(orm: Story) -> StorySummaryDTO:
    return StorySummaryDTO(
        id=str(orm.id),
        premise=orm.premise,
        label=orm.label,
        status=orm.status,
        review_score=orm.review_score,
        review_loops=orm.review_loops,
        error=orm.error,
        total_word_count=orm.total_word_count,
        created_at=orm.created_at,
        started_at=orm.started_at,
        completed_at=orm.completed_at,
    )


def story_to_detail(orm: Story) -> StoryDetailDTO:
    return StoryDetailDTO(
        id=str(orm.id),
        premise=orm.premise,
        label=orm.label,
        status=orm.status,
        bible_json=orm.bible_json,
        outline_json=orm.outline_json,
        review_score=orm.review_score,
        review_loops=orm.review_loops,
        error=orm.error,
        total_word_count=orm.total_word_count,
        created_at=orm.created_at,
        started_at=orm.started_at,
        completed_at=orm.completed_at,
        acts=[story_act_to_dto(a) for a in orm.acts],
    )


def patch_story_request_apply(orm: Story, req: PatchStoryRequest) -> None:
    """Mutate *orm* with only the fields explicitly set in *req*."""
    fields = req.model_fields_set
    if "status" in fields and req.status is not None:
        orm.status = req.status
    if "error" in fields:
        orm.error = req.error
    if "bible_json" in fields:
        orm.bible_json = req.bible_json  # type: ignore[assignment]
    if "outline_json" in fields:
        orm.outline_json = req.outline_json  # type: ignore[assignment]
    if "review_score" in fields:
        orm.review_score = req.review_score
    if "review_loops" in fields:
        orm.review_loops = req.review_loops  # type: ignore[assignment]
