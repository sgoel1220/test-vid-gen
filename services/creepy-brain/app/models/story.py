"""Story generation models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.enums import StoryStatus
from app.models.json_types import PydanticType
from app.models.json_schemas import StoryOutlineSchema


class Story(BaseModel):
    """Story generation tracking."""

    __tablename__ = "stories"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    premise: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[StoryOutlineSchema | None] = mapped_column(
        PydanticType(StoryOutlineSchema),
        nullable=True,
    )
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[StoryStatus] = mapped_column(
        SQLEnum(StoryStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=StoryStatus.PENDING,
    )
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    acts: Mapped[list["StoryAct"]] = relationship(
        "StoryAct",
        back_populates="story",
        cascade="all, delete-orphan",
        order_by="StoryAct.act_number",
    )


class StoryAct(BaseModel):
    """Individual act within a story."""

    __tablename__ = "story_acts"

    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    act_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    story: Mapped["Story"] = relationship("Story", back_populates="acts")

    __table_args__ = (UniqueConstraint("story_id", "act_number"),)
