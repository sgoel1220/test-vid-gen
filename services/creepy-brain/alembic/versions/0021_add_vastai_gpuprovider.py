"""Add vastai to gpuprovider PG enum.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0021"
down_revision: str = "0020"
branch_labels: None = None
depends_on: None = None

# Values present in gpuprovider before this migration
_GPUPROVIDER_PRE_0021 = ["runpod", "local", "modal"]

# Columns backed by gpuprovider: (table, column)
_GPUPROVIDER_COLUMNS = [
    ("gpu_pods", "provider"),
]


def upgrade() -> None:
    op.execute("ALTER TYPE gpuprovider ADD VALUE IF NOT EXISTS 'vastai'")


def downgrade() -> None:
    # PG does not support removing enum values directly; we must recreate the type.
    # Guard: refuse if any rows still carry the value being removed.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM gpu_pods WHERE provider::text = 'vastai')
            THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0021: rows exist with provider = vastai';
            END IF;
        END $$;
        """
    )

    # --- Recreate gpuprovider without vastai ---
    op.execute("ALTER TYPE gpuprovider RENAME TO gpuprovider_old")
    quoted = ", ".join(f"'{v}'" for v in _GPUPROVIDER_PRE_0021)
    op.execute(f"CREATE TYPE gpuprovider AS ENUM ({quoted})")
    for table, column in _GPUPROVIDER_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE gpuprovider "
            f"USING {column}::text::gpuprovider"
        )
    op.execute("DROP TYPE gpuprovider_old")
