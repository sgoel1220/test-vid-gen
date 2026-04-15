"""SQLAlchemy 2.x typed ORM models.

Import side-effect: registers all models against Base.metadata so that
alembic env.py can use autogenerate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from creepy_pasta_protocol.common import AudioFormat, RunStatus, StorageBackend
from creepy_pasta_protocol.settings import ResolvedSettingsSnapshot
from creepy_pasta_protocol.stories import StoryStatus
from creepy_pasta_protocol.validation import ChunkValidationSnapshot
from creepy_pasta_protocol.runs import RunWarnings

from app.db import Base
from app.types import PydanticJSONB


class AudioBlob(Base):
    __tablename__ = "audio_blobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    storage_backend: Mapped[StorageBackend] = mapped_column(
        Text().with_variant(Text(), "postgresql"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(Text(), nullable=False)
    sha256: Mapped[str] = mapped_column(Text(), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    mime_type: Mapped[str] = mapped_column(Text(), nullable=False)
    format: Mapped[AudioFormat] = mapped_column(
        Text().with_variant(Text(), "postgresql"), nullable=False
    )
    sample_rate: Mapped[int] = mapped_column(Integer(), nullable=False)
    duration_sec: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=4), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    # Back-references populated by relationship() on the owning side.
    voice: Mapped[Optional["Voice"]] = relationship(
        "Voice", back_populates="audio_blob", uselist=False
    )


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    text_sha256: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    char_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    runs: Mapped[List["Run"]] = relationship("Run", back_populates="script")


class Voice(Base):
    __tablename__ = "voices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    filename: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    audio_blob_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audio_blobs.id"), nullable=False
    )
    duration_sec: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=4), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    audio_blob: Mapped[AudioBlob] = relationship(
        "AudioBlob", back_populates="voice", foreign_keys=[audio_blob_id]
    )
    runs: Mapped[List["Run"]] = relationship("Run", back_populates="voice")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    script_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id"), nullable=False
    )
    voice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voices.id"), nullable=True
    )
    run_label: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    status: Mapped[RunStatus] = mapped_column(Text(), nullable=False)
    settings: Mapped[ResolvedSettingsSnapshot] = mapped_column(
        PydanticJSONB(ResolvedSettingsSnapshot), nullable=False
    )
    output_format: Mapped[AudioFormat] = mapped_column(Text(), nullable=False)
    source_chunk_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    selected_chunk_indices: Mapped[List[int]] = mapped_column(
        ARRAY(Integer()), nullable=False
    )
    normalized_text: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    warnings: Mapped[Optional[RunWarnings]] = mapped_column(
        PydanticJSONB(RunWarnings), nullable=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    final_audio_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audio_blobs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    pod_run_id: Mapped[Optional[str]] = mapped_column(Text(), nullable=True, unique=True)

    script: Mapped[Script] = relationship("Script", back_populates="runs")
    voice: Mapped[Optional[Voice]] = relationship(
        "Voice", back_populates="runs", foreign_keys=[voice_id]
    )
    final_audio: Mapped[Optional[AudioBlob]] = relationship(
        "AudioBlob", foreign_keys=[final_audio_id]
    )
    chunks: Mapped[List["Chunk"]] = relationship(
        "Chunk",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Chunk.chunk_index",
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("run_id", "chunk_index", name="uq_chunks_run_chunk_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    audio_blob_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audio_blobs.id"), nullable=True
    )
    attempts_used: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    validation: Mapped[Optional[ChunkValidationSnapshot]] = mapped_column(
        PydanticJSONB(ChunkValidationSnapshot), nullable=True
    )

    run: Mapped[Run] = relationship("Run", back_populates="chunks")
    audio_blob: Mapped[Optional[AudioBlob]] = relationship(
        "AudioBlob", foreign_keys=[audio_blob_id]
    )


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    premise: Mapped[str] = mapped_column(Text(), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    status: Mapped[StoryStatus] = mapped_column(Text(), nullable=False)
    bible_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    outline_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    review_score: Mapped[Optional[float]] = mapped_column(Float(), nullable=True)
    review_loops: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    total_word_count: Mapped[Optional[int]] = mapped_column(Integer(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    acts: Mapped[List["StoryAct"]] = relationship(
        "StoryAct",
        back_populates="story",
        cascade="all, delete-orphan",
        order_by="StoryAct.act_number",
    )


class StoryAct(Base):
    __tablename__ = "story_acts"
    __table_args__ = (
        UniqueConstraint("story_id", "act_number", name="uq_story_acts_story_act"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    act_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    title: Mapped[str] = mapped_column(Text(), nullable=False)
    target_word_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    story: Mapped[Story] = relationship("Story", back_populates="acts")
