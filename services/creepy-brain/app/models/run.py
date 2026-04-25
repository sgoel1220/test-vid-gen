"""TTS run models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import ChunkStatus, RunStatus


class Run(BaseModel):
    """TTS synthesis run."""

    __tablename__ = "runs"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    story_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="SET NULL"),
        nullable=True,
    )
    voice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("voices.id", ondelete="SET NULL"),
        nullable=True,
    )
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        SQLEnum(RunStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=RunStatus.PENDING,
    )
    final_audio_blob_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_blobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    total_duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    chunks: Mapped[list["RunChunk"]] = relationship(
        "RunChunk",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunChunk.chunk_index",
    )


class RunChunk(BaseModel):
    """Individual chunk within a TTS run."""

    __tablename__ = "run_chunks"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_blob_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_blobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[ChunkStatus] = mapped_column(
        SQLEnum(ChunkStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ChunkStatus.PENDING,
    )

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="chunks")

    __table_args__ = (UniqueConstraint("run_id", "chunk_index"),)
