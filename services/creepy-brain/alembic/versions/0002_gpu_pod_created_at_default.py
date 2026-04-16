"""Add server_default to gpu_pods.created_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-16

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill any existing rows that have NULL (shouldn't exist given nullable=False,
    # but guard against data loaded outside the ORM).
    op.execute("UPDATE gpu_pods SET created_at = now() WHERE created_at IS NULL")

    op.alter_column(
        "gpu_pods",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "gpu_pods",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
