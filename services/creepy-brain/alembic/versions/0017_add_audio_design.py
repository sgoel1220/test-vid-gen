"""Add music_generation to stepname and music_audio/music_bed to blobtype PG enums.

sfx_generation and sfx_audio were already added in 0016; this migration adds
the remaining audio-design enum values for the music pipeline.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op

revision: str = "0017"
down_revision: str = "0016"
branch_labels: None = None
depends_on: None = None

# Values present in stepname after 0016 (excluding music_generation added here)
_STEPNAME_PRE_0017 = [
    "generate_story",
    "tts_synthesis",
    "image_generation",
    "stitch_final",
    "cleanup_gpu_pod",
    "step_one",
    "step_two",
    "recon_orphaned_pods",
    "waveform_overlay",
    "sfx_generation",
]

# Values present in blobtype after 0016 (excluding music_audio/music_bed added here)
_BLOBTYPE_PRE_0017 = [
    "chunk_audio",
    "chunk_audio_mp3",
    "final_audio",
    "image",
    "final_video",
    "waveform_video",
    "voice_audio",
    "sfx_audio",
]

# Columns backed by stepname: (table, column)
_STEPNAME_COLUMNS = [
    ("workflows", "current_step"),
    ("workflow_steps", "step_name"),
]

# Columns backed by blobtype: (table, column)
_BLOBTYPE_COLUMNS = [
    ("workflow_blobs", "blob_type"),
]


def upgrade() -> None:
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'music_generation'")
    op.execute("ALTER TYPE blobtype ADD VALUE IF NOT EXISTS 'music_audio'")
    op.execute("ALTER TYPE blobtype ADD VALUE IF NOT EXISTS 'music_bed'")


def downgrade() -> None:
    # PG does not support removing enum values directly; we must recreate the type.
    # Guard: refuse if any rows still carry the values being removed.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM workflows WHERE current_step::text = 'music_generation')
               OR EXISTS (SELECT 1 FROM workflow_steps WHERE step_name::text = 'music_generation')
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0017: rows exist with step_name/current_step = music_generation';
            END IF;
            IF EXISTS (SELECT 1 FROM workflow_blobs WHERE blob_type::text IN ('music_audio', 'music_bed'))
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0017: rows exist with blob_type in (music_audio, music_bed)';
            END IF;
        END $$;
        """
    )

    # --- Recreate stepname without music_generation ---
    op.execute("ALTER TYPE stepname RENAME TO stepname_old")
    quoted_stepname = ", ".join(f"'{v}'" for v in _STEPNAME_PRE_0017)
    op.execute(f"CREATE TYPE stepname AS ENUM ({quoted_stepname})")
    for table, column in _STEPNAME_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE stepname "
            f"USING {column}::text::stepname"
        )
    op.execute("DROP TYPE stepname_old")

    # --- Recreate blobtype without music_audio and music_bed ---
    op.execute("ALTER TYPE blobtype RENAME TO blobtype_old")
    quoted_blobtype = ", ".join(f"'{v}'" for v in _BLOBTYPE_PRE_0017)
    op.execute(f"CREATE TYPE blobtype AS ENUM ({quoted_blobtype})")
    for table, column in _BLOBTYPE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE blobtype "
            f"USING {column}::text::blobtype"
        )
    op.execute("DROP TYPE blobtype_old")
