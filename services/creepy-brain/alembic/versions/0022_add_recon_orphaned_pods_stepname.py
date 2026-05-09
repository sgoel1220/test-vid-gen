"""Add recon_orphaned_pods to stepname PG enum.

Migration 0014 intended to add this value but it is absent from the live
enum (verified via pg_enum). This migration adds it unconditionally using
IF NOT EXISTS so it is safe to re-run.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-09
"""

from __future__ import annotations

from alembic import op

revision: str = "0022"
down_revision: str = "0021"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'recon_orphaned_pods'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
