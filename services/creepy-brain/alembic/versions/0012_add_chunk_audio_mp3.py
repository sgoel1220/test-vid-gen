"""Add chunk_audio_mp3 blob type and tts_mp3_blob_id column.

Adds:
  - 'chunk_audio_mp3' value to the blobtype PG enum
  - workflow_chunks.tts_mp3_blob_id nullable UUID FK -> workflow_blobs.id

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new value to existing PG enum (non-transactional DDL — must run outside tx)
    op.execute("ALTER TYPE blobtype ADD VALUE IF NOT EXISTS 'chunk_audio_mp3'")

    # Add tts_mp3_blob_id column to workflow_chunks
    op.add_column(
        "workflow_chunks",
        sa.Column(
            "tts_mp3_blob_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_blobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_workflow_chunks_mp3_blob",
        "workflow_chunks",
        ["tts_mp3_blob_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_workflow_chunks_mp3_blob", table_name="workflow_chunks")
    op.drop_column("workflow_chunks", "tts_mp3_blob_id")
    # Note: PG enum values cannot be removed without dropping and recreating the type
