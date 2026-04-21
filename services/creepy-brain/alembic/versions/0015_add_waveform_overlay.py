"""Add waveform_video to blobtype and waveform_overlay to stepname PG enums.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op

revision: str = "0015"
down_revision: str = "0014"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE blobtype ADD VALUE IF NOT EXISTS 'waveform_video'")
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'waveform_overlay'")


def downgrade() -> None:
    # PG does not support removing values from an enum type.
    # A full enum recreation would be needed; left as no-op for dev workflows.
    pass
