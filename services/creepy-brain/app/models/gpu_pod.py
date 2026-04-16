"""GPU pod tracking model."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import GpuPodStatus, GpuProvider


class GpuPod(Base):
    """GPU pod tracking for cost monitoring."""

    __tablename__ = "gpu_pods"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[GpuProvider] = mapped_column(
        SQLEnum(GpuProvider, native_enum=False, length=20),
        nullable=False,
    )
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    endpoint_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[GpuPodStatus] = mapped_column(
        SQLEnum(GpuPodStatus, native_enum=False, length=20),
        nullable=False,
    )
    gpu_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cost_per_hour_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_gpu_pods_status", "status"),
        Index("idx_gpu_pods_workflow", "workflow_id"),
    )
