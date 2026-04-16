"""Document addition of VOICE_AUDIO to BlobType enum.

No DDL change — blob_type is stored as VARCHAR(20) (native_enum=False),
so any valid enum string is accepted without schema changes.
This migration documents the new value in the migration history.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op  # noqa: F401

revision: str = "0004"
down_revision: str = "0003"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # No DDL required: blob_type column is VARCHAR(20), not a native pg enum.
    # VOICE_AUDIO = "voice_audio" is a valid value as-is.
    pass


def downgrade() -> None:
    pass
