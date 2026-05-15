"""Add music_prompt, music_intensity, music_tts_intensity to workflow_scenes.

Persists LLM-generated music mood prompts so retries skip redundant API calls.
Mirrors the image_prompt pattern in the same table.

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str = "0022"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("workflow_scenes", sa.Column("music_prompt", sa.Text(), nullable=True))
    op.add_column("workflow_scenes", sa.Column("music_intensity", sa.Integer(), nullable=True))
    op.add_column("workflow_scenes", sa.Column("music_tts_intensity", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_scenes", "music_tts_intensity")
    op.drop_column("workflow_scenes", "music_intensity")
    op.drop_column("workflow_scenes", "music_prompt")
