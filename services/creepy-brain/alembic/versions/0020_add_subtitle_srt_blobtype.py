"""Add subtitle_srt to blobtype PG enum.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op

revision: str = "0020"
down_revision: str = "0019"
branch_labels: None = None
depends_on: None = None

# Values present in blobtype after 0017 (before this migration)
_BLOBTYPE_PRE_0020 = [
    "chunk_audio",
    "chunk_audio_mp3",
    "final_audio",
    "image",
    "final_video",
    "waveform_video",
    "voice_audio",
    "sfx_audio",
    "music_audio",
    "music_bed",
]

# Columns backed by blobtype: (table, column)
_BLOBTYPE_COLUMNS = [
    ("workflow_blobs", "blob_type"),
]


def upgrade() -> None:
    op.execute("ALTER TYPE blobtype ADD VALUE IF NOT EXISTS 'subtitle_srt'")


def downgrade() -> None:
    # PG does not support removing enum values directly; we must recreate the type.
    # Guard: refuse if any rows still carry the value being removed.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM workflow_blobs WHERE blob_type::text = 'subtitle_srt')
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0020: rows exist with blob_type = subtitle_srt';
            END IF;
        END $$;
        """
    )

    # --- Recreate blobtype without subtitle_srt ---
    op.execute("ALTER TYPE blobtype RENAME TO blobtype_old")
    quoted_blobtype = ", ".join(f"'{v}'" for v in _BLOBTYPE_PRE_0020)
    op.execute(f"CREATE TYPE blobtype AS ENUM ({quoted_blobtype})")
    for table, column in _BLOBTYPE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE blobtype "
            f"USING {column}::text::blobtype"
        )
    op.execute("DROP TYPE blobtype_old")
