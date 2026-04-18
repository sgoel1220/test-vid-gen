"""Convert VARCHAR enum columns to native PostgreSQL enum types.

Replaces VARCHAR + CHECK constraint pattern with native PG enums for
static type safety at the database level. Every enum column gets a
dedicated PG enum type matching the Python enum name.

Affected tables and columns:
  workflows.workflow_type      → workflowtype
  workflows.status             → workflowstatus
  workflows.current_step       → stepname
  workflow_steps.step_name     → stepname
  workflow_steps.status        → stepstatus
  workflow_chunks.tts_status   → chunkstatus
  workflow_scenes.image_status → chunkstatus
  workflow_blobs.blob_type     → blobtype
  gpu_pods.provider            → gpuprovider
  gpu_pods.status              → gpupodstatus
  stories.status               → storystatus
  runs.status                  → runstatus
  run_chunks.status            → chunkstatus

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: str = "0010"
branch_labels: None = None
depends_on: None = None

# ---------------------------------------------------------------------------
# PG enum type name → list of allowed values (matches Python Enum .value)
# ---------------------------------------------------------------------------
_PG_ENUMS: dict[str, list[str]] = {
    "workflowtype": ["content_pipeline"],
    "workflowstatus": ["pending", "running", "completed", "failed", "cancelled", "paused"],
    "stepname": ["generate_story", "tts_synthesis", "image_generation", "stitch_final", "cleanup_gpu_pod"],
    "stepstatus": ["pending", "running", "completed", "failed", "skipped"],
    "chunkstatus": ["pending", "processing", "completed", "failed"],
    "blobtype": ["chunk_audio", "final_audio", "image", "final_video", "voice_audio"],
    "gpuprovider": ["runpod", "local", "modal"],
    "gpupodstatus": ["creating", "running", "ready", "terminated", "error"],
    "storystatus": ["pending", "generating", "reviewing", "completed", "failed"],
    "runstatus": ["pending", "processing", "completed", "failed"],
}

# ---------------------------------------------------------------------------
# (table, column, pg_enum_type_name, check_constraint_to_drop, default_value)
# default_value: None means no default; str means re-add after conversion
# ---------------------------------------------------------------------------
_COLUMNS: list[tuple[str, str, str, str | None, str | None]] = [
    ("workflows", "workflow_type", "workflowtype", "ck_workflows_workflow_type", None),
    ("workflows", "status", "workflowstatus", "ck_workflows_status", "pending"),
    ("workflows", "current_step", "stepname", "ck_workflows_current_step", None),
    ("workflow_steps", "step_name", "stepname", "ck_workflow_steps_step_name", None),
    ("workflow_steps", "status", "stepstatus", "ck_workflow_steps_status", "pending"),
    ("workflow_chunks", "tts_status", "chunkstatus", "ck_workflow_chunks_tts_status", "pending"),
    ("workflow_scenes", "image_status", "chunkstatus", "ck_workflow_scenes_image_status", "pending"),
    ("workflow_blobs", "blob_type", "blobtype", "ck_workflow_blobs_blob_type", None),
    ("gpu_pods", "provider", "gpuprovider", "ck_gpu_pods_provider", None),
    ("gpu_pods", "status", "gpupodstatus", "ck_gpu_pods_status", None),
    ("stories", "status", "storystatus", "ck_stories_status", "pending"),
    ("runs", "status", "runstatus", "ck_runs_status", "pending"),
    ("run_chunks", "status", "chunkstatus", "ck_run_chunks_status", "pending"),
]


def upgrade() -> None:
    # 1. Create all PG enum types
    for type_name, values in _PG_ENUMS.items():
        quoted = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {type_name} AS ENUM ({quoted})")

    # 2. Drop check constraints, drop defaults, convert columns, re-add defaults
    for table, column, pg_type, ck_name, default_val in _COLUMNS:
        if ck_name:
            op.drop_constraint(ck_name, table, type_="check")

        # Must drop VARCHAR default before type conversion
        if default_val is not None:
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"
            )

        # Convert VARCHAR → native enum
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE {pg_type} "
            f"USING {column}::{pg_type}"
        )

        # Re-add default as enum value
        if default_val is not None:
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {column} SET DEFAULT '{default_val}'::{pg_type}"
            )


def downgrade() -> None:
    for table, column, pg_type, ck_name, default_val in reversed(_COLUMNS):
        if default_val is not None:
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"
            )

        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE VARCHAR(50) "
            f"USING {column}::text"
        )

        if default_val is not None:
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {column} SET DEFAULT '{default_val}'"
            )

        if ck_name:
            values = _PG_ENUMS[pg_type]
            quoted = ", ".join(f"'{v}'" for v in values)
            expr = f"{column} IS NULL OR {column} IN ({quoted})"
            op.create_check_constraint(ck_name, table, expr)

    # Drop enum types
    for type_name in reversed(list(_PG_ENUMS.keys())):
        op.execute(f"DROP TYPE {type_name}")
