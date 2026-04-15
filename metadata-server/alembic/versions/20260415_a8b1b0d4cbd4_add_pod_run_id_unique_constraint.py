"""add_pod_run_id_unique_constraint

Revision ID: a8b1b0d4cbd4
Revises: 0001
Create Date: 2026-04-15 20:14:29.076622+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8b1b0d4cbd4'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unique constraint on pod_run_id for idempotency
    op.create_unique_constraint('uq_runs_pod_run_id', 'runs', ['pod_run_id'])


def downgrade() -> None:
    # Drop unique constraint on pod_run_id
    op.drop_constraint('uq_runs_pod_run_id', 'runs', type_='unique')
