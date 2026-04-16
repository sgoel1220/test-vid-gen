"""Initial schema: audio_blobs, scripts, voices, runs, chunks.

Revision ID: 0001
Revises:
Create Date: 2026-04-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # --- audio_blobs ---
    op.create_table(
        "audio_blobs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("storage_backend", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("duration_sec", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audio_blobs_sha256", "audio_blobs", ["sha256"])

    # --- scripts ---
    op.create_table(
        "scripts",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # --- voices ---
    op.create_table(
        "voices",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False, unique=True),
        sa.Column("audio_blob_id", sa.UUID(), sa.ForeignKey("audio_blobs.id"), nullable=False),
        sa.Column("duration_sec", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # --- runs ---
    op.create_table(
        "runs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("script_id", sa.UUID(), sa.ForeignKey("scripts.id"), nullable=False),
        sa.Column("voice_id", sa.UUID(), sa.ForeignKey("voices.id"), nullable=True),
        sa.Column("run_label", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("settings", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("output_format", sa.Text(), nullable=False),
        sa.Column("source_chunk_count", sa.Integer(), nullable=False),
        sa.Column("selected_chunk_indices", sa.ARRAY(sa.Integer()), nullable=False, server_default="{}"),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("warnings", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "final_audio_id",
            sa.UUID(),
            sa.ForeignKey("audio_blobs.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pod_run_id", sa.Text(), nullable=True),
    )
    op.create_index("ix_runs_created_at_desc", "runs", [sa.text("created_at DESC")])

    # --- chunks ---
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("audio_blob_id", sa.UUID(), sa.ForeignKey("audio_blobs.id"), nullable=True),
        sa.Column("attempts_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("run_id", "chunk_index", name="uq_chunks_run_chunk_index"),
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_index("ix_runs_created_at_desc", table_name="runs")
    op.drop_table("runs")
    op.drop_table("voices")
    op.drop_table("scripts")
    op.drop_index("ix_audio_blobs_sha256", table_name="audio_blobs")
    op.drop_table("audio_blobs")
