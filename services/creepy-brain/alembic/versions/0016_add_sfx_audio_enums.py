"""Add sfx_audio to blobtype and sfx_generation to stepname PG enums.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op

revision: str = "0016"
down_revision: str = "0015"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE blobtype ADD VALUE IF NOT EXISTS 'sfx_audio'")
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'sfx_generation'")


def downgrade() -> None:
    # PG does not support removing values from an enum type.
    # A full enum recreation would be needed; left as no-op for dev workflows.
    pass
