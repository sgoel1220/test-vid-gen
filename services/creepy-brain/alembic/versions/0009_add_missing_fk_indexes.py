"""Add missing indexes for foreign-key and filter query paths.

Postgres does not automatically index FK columns.  The following hot or
likely-hot query paths were unindexed:

  runs.workflow_id    — FK to workflows; used in workflow-level run lookups
  runs.story_id       — FK to stories; used in story-level run lookups
  runs.voice_id       — FK to voices; used in voice-level run lookups
  runs.final_audio_blob_id — FK to workflow_blobs
  run_chunks.audio_blob_id — FK to workflow_blobs

  stories.workflow_id — FK to workflows; used in story-to-workflow joins
  story_acts.story_id — FK to stories; already covered by the unique
                        constraint (story_id, act_number), BUT some Postgres
                        versions do not use the partial unique index for plain
                        FK lookups so an explicit index is added defensively.

Composite indexes:
  workflows(status, created_at) — hot filter path for list/status endpoints
  runs(status, created_at)      — same pattern for run list endpoints

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op

revision: str = "0009"
down_revision: str = "0008"
branch_labels: None = None
depends_on: None = None

# ---------------------------------------------------------------------------
# (table, index_name, columns)
# ---------------------------------------------------------------------------
_SINGLE_INDEXES: list[tuple[str, str, list[str]]] = [
    # runs FK columns
    ("runs", "idx_runs_workflow_id", ["workflow_id"]),
    ("runs", "idx_runs_story_id", ["story_id"]),
    ("runs", "idx_runs_voice_id", ["voice_id"]),
    ("runs", "idx_runs_final_audio_blob", ["final_audio_blob_id"]),
    # run_chunks FK column
    ("run_chunks", "idx_run_chunks_audio_blob", ["audio_blob_id"]),
    # stories FK column
    ("stories", "idx_stories_workflow_id", ["workflow_id"]),
    # story_acts FK column (defensive duplicate of unique constraint prefix)
    ("story_acts", "idx_story_acts_story_id", ["story_id"]),
]

# Composite indexes — column order matters for Postgres partial-match scans.
_COMPOSITE_INDEXES: list[tuple[str, str, list[str]]] = [
    ("workflows", "idx_workflows_status_created", ["status", "created_at"]),
    ("runs", "idx_runs_status_created", ["status", "created_at"]),
]


def upgrade() -> None:
    for table, name, columns in _SINGLE_INDEXES:
        op.create_index(name, table, columns)
    for table, name, columns in _COMPOSITE_INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for table, name, _columns in reversed(_COMPOSITE_INDEXES):
        op.drop_index(name, table_name=table)
    for table, name, _columns in reversed(_SINGLE_INDEXES):
        op.drop_index(name, table_name=table)
