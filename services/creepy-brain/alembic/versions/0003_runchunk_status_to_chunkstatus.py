"""Use ChunkStatus enum for RunChunk.status (was RunStatus).

No DDL change — the column is stored as VARCHAR(20) (native_enum=False) and
both RunStatus and ChunkStatus share identical string values. This migration
documents the semantic correction in the migration history.

Revision ID: 0003
Revises: 0001
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op  # noqa: F401

# revision identifiers
revision: str = "0003"
down_revision: str = "0002"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # No DDL required: column type is VARCHAR(20) in both cases.
    pass


def downgrade() -> None:
    pass
