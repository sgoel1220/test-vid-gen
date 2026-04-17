"""TTS run models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import ChunkStatus, RunStatus


class Run(BaseModel):
    """TTS synthesis run."""

    __tablename__ = "runs"

    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    story_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="SET NULL"),
        nullable=True,
    )
    voice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("voices.id", ondelete="SET NULL"),
        nullable=True,
    )
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        SQLEnum(RunStatus, native_enum=False, length=20),
        nullable=False,
        default=RunStatus.PENDING,
    )
    final_audio_blob_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    total_duration_sec: Mapped[Optional[float]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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
    audio_blob_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    duration_sec: Mapped[Optional[float]] = mapped_column(nullable=True)
    status: Mapped[ChunkStatus] = mapped_column(
        SQLEnum(ChunkStatus, native_enum=False, length=20),
        nullable=False,
        default=ChunkStatus.PENDING,
    )

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="chunks")

    __table_args__ = (UniqueConstraint("run_id", "chunk_index"),)
