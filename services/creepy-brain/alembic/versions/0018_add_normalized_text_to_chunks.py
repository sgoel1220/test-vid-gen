"""Add normalized_text column to workflow_chunks and run_chunks.

Stores the LLM-normalized text alongside the original chunk text so that
the normalize_text() LLM call can be skipped on retries.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str = "0017"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column(
        "workflow_chunks",
        sa.Column("normalized_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "run_chunks",
        sa.Column("normalized_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_chunks", "normalized_text")
    op.drop_column("workflow_chunks", "normalized_text")
