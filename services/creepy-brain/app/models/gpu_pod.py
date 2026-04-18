"""GPU pod tracking model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import GpuPodStatus, GpuProvider


class GpuPod(Base):
    """GPU pod tracking for cost monitoring."""

    __tablename__ = "gpu_pods"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[GpuProvider] = mapped_column(
        SQLEnum(GpuProvider, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[GpuPodStatus] = mapped_column(
        SQLEnum(GpuPodStatus, native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    gpu_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cost_per_hour_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_gpu_pods_status", "status"),
        Index("idx_gpu_pods_workflow", "workflow_id"),
    )
