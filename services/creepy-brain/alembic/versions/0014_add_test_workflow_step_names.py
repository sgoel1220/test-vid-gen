"""Add step_one, step_two, recon_orphaned_pods to stepname PG enum.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op

revision: str = "0014"
down_revision: str = "0013"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'step_one'")
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'step_two'")
    op.execute("ALTER TYPE stepname ADD VALUE IF NOT EXISTS 'recon_orphaned_pods'")


def downgrade() -> None:
    # PG does not support removing values from an enum type.
    # A full enum recreation would be needed; left as no-op for dev workflows.
    pass
