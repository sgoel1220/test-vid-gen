"""Add DB check constraints for enum fields and non-negative numeric columns.

Protects against invalid states being inserted via direct SQL, future
services, or partial failure handling even when Python/Pydantic code is
correct.

Enum constraints:
  workflows.status, workflows.workflow_type, workflows.current_step
  workflow_steps.status, workflow_steps.step_name
  workflow_chunks.tts_status
  workflow_scenes.image_status
  workflow_blobs.blob_type
  gpu_pods.status, gpu_pods.provider
  stories.status
  runs.status (run_chunks table)
  run_chunks.status

Non-negative / range constraints:
  workflow_steps.attempt_number >= 1
  workflow_chunks.chunk_index >= 0
  workflow_scenes.scene_index >= 0
  workflow_blobs.size_bytes >= 0
  stories.word_count >= 0, stories.total_tokens_used >= 0
  story_acts.act_number >= 1, story_acts.word_count >= 0,
      story_acts.revision_count >= 0

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op

revision: str = "0008"
down_revision: str = "0007"
branch_labels: None = None
depends_on: None = None

# ---------------------------------------------------------------------------
# Enum check constraints
# ---------------------------------------------------------------------------
# (table, constraint_name, column, allowed_values)
_ENUM_CHECKS: list[tuple[str, str, str, list[str]]] = [
    # workflows
    (
        "workflows",
        "ck_workflows_status",
        "status",
        ["pending", "running", "completed", "failed", "cancelled", "paused"],
    ),
    (
        "workflows",
        "ck_workflows_workflow_type",
        "workflow_type",
        ["content_pipeline"],
    ),
    (
        "workflows",
        "ck_workflows_current_step",
        "current_step",
        ["generate_story", "tts_synthesis", "image_generation", "stitch_final", "cleanup_gpu_pod"],
    ),
    # workflow_steps
    (
        "workflow_steps",
        "ck_workflow_steps_status",
        "status",
        ["pending", "running", "completed", "failed", "skipped"],
    ),
    (
        "workflow_steps",
        "ck_workflow_steps_step_name",
        "step_name",
        ["generate_story", "tts_synthesis", "image_generation", "stitch_final", "cleanup_gpu_pod"],
    ),
    # workflow_chunks
    (
        "workflow_chunks",
        "ck_workflow_chunks_tts_status",
        "tts_status",
        ["pending", "processing", "completed", "failed"],
    ),
    # workflow_scenes
    (
        "workflow_scenes",
        "ck_workflow_scenes_image_status",
        "image_status",
        ["pending", "processing", "completed", "failed"],
    ),
    # workflow_blobs
    (
        "workflow_blobs",
        "ck_workflow_blobs_blob_type",
        "blob_type",
        ["chunk_audio", "final_audio", "image", "final_video", "voice_audio"],
    ),
    # gpu_pods
    (
        "gpu_pods",
        "ck_gpu_pods_status",
        "status",
        ["creating", "running", "ready", "terminated", "error"],
    ),
    (
        "gpu_pods",
        "ck_gpu_pods_provider",
        "provider",
        ["runpod", "local", "modal"],
    ),
    # stories
    (
        "stories",
        "ck_stories_status",
        "status",
        ["pending", "generating", "reviewing", "completed", "failed"],
    ),
    # runs
    (
        "runs",
        "ck_runs_status",
        "status",
        ["pending", "processing", "completed", "failed"],
    ),
    # run_chunks
    (
        "run_chunks",
        "ck_run_chunks_status",
        "status",
        ["pending", "processing", "completed", "failed"],
    ),
]

# ---------------------------------------------------------------------------
# Non-negative / range constraints
# (table, constraint_name, expression)
# ---------------------------------------------------------------------------
_RANGE_CHECKS: list[tuple[str, str, str]] = [
    ("workflow_steps", "ck_workflow_steps_attempt_gte1", "attempt_number >= 1"),
    ("workflow_chunks", "ck_workflow_chunks_index_gte0", "chunk_index >= 0"),
    ("workflow_scenes", "ck_workflow_scenes_index_gte0", "scene_index >= 0"),
    ("workflow_blobs", "ck_workflow_blobs_size_gte0", "size_bytes >= 0"),
    ("stories", "ck_stories_word_count_gte0", "word_count >= 0"),
    ("stories", "ck_stories_tokens_gte0", "total_tokens_used >= 0"),
    ("story_acts", "ck_story_acts_number_gte1", "act_number >= 1"),
    ("story_acts", "ck_story_acts_word_count_gte0", "word_count >= 0"),
    ("story_acts", "ck_story_acts_revision_gte0", "revision_count >= 0"),
]


def _enum_check_expr(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IS NULL OR {column} IN ({quoted})"


def upgrade() -> None:
    # Normalize existing data: uppercase enum values → lowercase to match
    # Python Enum .value definitions (e.g. WorkflowStatus.RUNNING = "running").
    for table, _name, column, _values in _ENUM_CHECKS:
        op.execute(
            f"UPDATE {table} SET {column} = LOWER({column}) "
            f"WHERE {column} IS NOT NULL AND {column} != LOWER({column})"
        )

    for table, name, column, values in _ENUM_CHECKS:
        op.create_check_constraint(
            name,
            table,
            _enum_check_expr(column, values),
        )
    for table, name, expr in _RANGE_CHECKS:
        op.create_check_constraint(name, table, expr)


def downgrade() -> None:
    for table, name, _expr in reversed(_RANGE_CHECKS):
        op.drop_constraint(name, table, type_="check")
    for table, name, _column, _values in reversed(_ENUM_CHECKS):
        op.drop_constraint(name, table, type_="check")
