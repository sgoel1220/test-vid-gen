"""Add voice audio_blob_id FK, relax audio_path to nullable, enforce single default voice.

Changes:
- voices.audio_path: change NOT NULL -> NULL (built-in file voices use it;
  uploaded voices use audio_blob_id instead).
- voices.audio_blob_id: new nullable UUID column with FK -> workflow_blobs.id.
- idx_voices_audio_blob: index on voices.audio_blob_id.
- uq_voices_single_default: partial unique index so at most one voice can have
  is_default = TRUE.

Backfill: rows that stored a blob UUID string in audio_path (uploaded voices)
are migrated to use audio_blob_id; audio_path is set to NULL for those rows.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str = "0006"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # 1. Add audio_blob_id column (nullable UUID FK).
    op.add_column(
        "voices",
        sa.Column(
            "audio_blob_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_voices_audio_blob",
        "voices",
        "workflow_blobs",
        ["audio_blob_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_voices_audio_blob", "voices", ["audio_blob_id"])

    # 2. Relax audio_path to nullable FIRST (must happen before backfill
    #    sets audio_path = NULL for uploaded voices).
    op.alter_column("voices", "audio_path", nullable=True)

    # 3. Backfill: any row where audio_path looks like a UUID is an uploaded
    #    voice that stored str(blob.id) in the wrong column.  Move it to
    #    audio_blob_id and clear audio_path.
    op.execute(
        """
        UPDATE voices
        SET audio_blob_id = audio_path::uuid,
            audio_path     = NULL
        WHERE audio_path ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        """
    )

    # 4. Deduplicate defaults: keep only the most recently created default voice.
    op.execute(
        """
        UPDATE voices
        SET is_default = FALSE
        WHERE is_default = TRUE
          AND id != (
              SELECT id FROM voices
              WHERE is_default = TRUE
              ORDER BY created_at DESC
              LIMIT 1
          )
        """
    )

    # 5. Partial unique index: only one is_default = TRUE row permitted.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_voices_single_default
        ON voices (is_default)
        WHERE is_default = TRUE
        """
    )


def downgrade() -> None:
    # Drop partial unique index.
    op.execute("DROP INDEX IF EXISTS uq_voices_single_default")

    # Restore audio_path to NOT NULL: backfill NULLs with the blob ID string.
    op.execute(
        """
        UPDATE voices
        SET audio_path     = audio_blob_id::text,
            audio_blob_id  = NULL
        WHERE audio_path IS NULL AND audio_blob_id IS NOT NULL
        """
    )
    op.alter_column("voices", "audio_path", nullable=False)

    # Drop the new FK and column.
    op.drop_index("idx_voices_audio_blob", table_name="voices")
    op.drop_constraint("fk_voices_audio_blob", "voices", type_="foreignkey")
    op.drop_column("voices", "audio_blob_id")
