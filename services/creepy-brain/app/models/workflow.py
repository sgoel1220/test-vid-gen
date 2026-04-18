"""Workflow orchestration models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import (
    BlobType,
    ChunkStatus,
    StepName,
    StepStatus,
    WorkflowStatus,
    WorkflowType,
)
from app.models.json_types import PydanticType
from app.models.json_schemas import (
    StepInputSchema,
    StepOutputSchema,
    WorkflowInputSchema,
    WorkflowResultSchema,
)


class Workflow(BaseModel):
    """Workflow execution tracking."""

    __tablename__ = "workflows"

    workflow_type: Mapped[WorkflowType] = mapped_column(
        SQLEnum(WorkflowType, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    input_json: Mapped[WorkflowInputSchema] = mapped_column(
        PydanticType(WorkflowInputSchema),
        nullable=False,
    )
    status: Mapped[WorkflowStatus] = mapped_column(
        SQLEnum(WorkflowStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=WorkflowStatus.PENDING,
    )
    current_step: Mapped[StepName | None] = mapped_column(
        SQLEnum(StepName, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    result_json: Mapped[WorkflowResultSchema | None] = mapped_column(
        PydanticType(WorkflowResultSchema),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    steps: Mapped[list["WorkflowStep"]] = relationship(
        "WorkflowStep",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list["WorkflowChunk"]] = relationship(
        "WorkflowChunk",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
    scenes: Mapped[list["WorkflowScene"]] = relationship(
        "WorkflowScene",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
    blobs: Mapped[list["WorkflowBlob"]] = relationship(
        "WorkflowBlob",
        back_populates="workflow",
    )

    __table_args__ = (
        Index("idx_workflows_status", "status"),
        Index("idx_workflows_created", "created_at"),
    )


class WorkflowStep(BaseModel):
    """Individual step execution within a workflow."""

    __tablename__ = "workflow_steps"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name: Mapped[StepName] = mapped_column(
        SQLEnum(StepName, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    status: Mapped[StepStatus] = mapped_column(
        SQLEnum(StepStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=StepStatus.PENDING,
    )
    # Discriminated union types with Pydantic validation
    input_json: Mapped[StepInputSchema | None] = mapped_column(
        PydanticType(StepInputSchema),  # type: ignore[arg-type]  # Annotated discriminated union
        nullable=True,
    )
    output_json: Mapped[StepOutputSchema | None] = mapped_column(
        PydanticType(StepOutputSchema),  # type: ignore[arg-type]  # Annotated discriminated union
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    gpu_pod_id: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("gpu_pods.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow", back_populates="steps")

    __table_args__ = (
        UniqueConstraint("workflow_id", "step_name", "attempt_number"),
        Index("idx_workflow_steps_workflow", "workflow_id"),
    )



class WorkflowScene(BaseModel):
    """Image scene - one per N story chunks (default 7)."""

    __tablename__ = "workflow_scenes"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Image prompt (saved before GPU spin-up)
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Image generation result
    image_status: Mapped[ChunkStatus] = mapped_column(
        SQLEnum(ChunkStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ChunkStatus.PENDING,
    )
    image_blob_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_blobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    image_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow", back_populates="scenes")
    chunks: Mapped[list["WorkflowChunk"]] = relationship("WorkflowChunk", back_populates="scene")

    __table_args__ = (
        UniqueConstraint("workflow_id", "scene_index"),
        Index("idx_workflow_scenes_workflow", "workflow_id"),
    )

class WorkflowChunk(BaseModel):
    """Story chunk - one per TTS segment."""

    __tablename__ = "workflow_chunks"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)

    # TTS fields
    tts_status: Mapped[ChunkStatus] = mapped_column(
        SQLEnum(ChunkStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ChunkStatus.PENDING,
    )
    tts_audio_blob_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_blobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    tts_duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    tts_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Link to scene (for image)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_scenes.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow", back_populates="chunks")
    scene: Mapped[WorkflowScene | None] = relationship("WorkflowScene", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("workflow_id", "chunk_index"),
        Index("idx_workflow_chunks_workflow", "workflow_id"),
        Index("idx_workflow_chunks_scene", "scene_id"),
    )


class WorkflowBlob(BaseModel):
    """Binary blob storage for audio/images."""

    __tablename__ = "workflow_blobs"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    blob_type: Mapped[BlobType] = mapped_column(
        SQLEnum(BlobType, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    data: Mapped[bytes] = mapped_column(nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    workflow: Mapped[Workflow | None] = relationship("Workflow", back_populates="blobs")

    __table_args__ = (Index("idx_workflow_blobs_workflow", "workflow_id"),)
