"""Add FK constraints for blob and pod reference columns.

Five UUID/string reference columns lacked FK constraints, allowing
orphaned or dangling references:
- workflow_chunks.tts_audio_blob_id -> workflow_blobs.id
- workflow_scenes.image_blob_id -> workflow_blobs.id
- run_chunks.audio_blob_id -> workflow_blobs.id
- runs.final_audio_blob_id -> workflow_blobs.id
- workflow_steps.gpu_pod_id -> gpu_pods.id

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels: None = None
depends_on: None = None

# (source_table, constraint_name, local_col, ref_table, ref_col)
_FK_SPECS: list[tuple[str, str, str, str, str]] = [
    ("workflow_chunks", "fk_workflow_chunks_tts_audio_blob", "tts_audio_blob_id", "workflow_blobs", "id"),
    ("workflow_scenes", "fk_workflow_scenes_image_blob", "image_blob_id", "workflow_blobs", "id"),
    ("run_chunks", "fk_run_chunks_audio_blob", "audio_blob_id", "workflow_blobs", "id"),
    ("runs", "fk_runs_final_audio_blob", "final_audio_blob_id", "workflow_blobs", "id"),
    ("workflow_steps", "fk_workflow_steps_gpu_pod", "gpu_pod_id", "gpu_pods", "id"),
]


def upgrade() -> None:
    for source_table, constraint_name, local_col, ref_table, ref_col in _FK_SPECS:
        op.create_foreign_key(
            constraint_name,
            source_table,
            ref_table,
            [local_col],
            [ref_col],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for source_table, constraint_name, *_ in reversed(_FK_SPECS):
        op.drop_constraint(constraint_name, source_table, type_="foreignkey")
