"""Add cleanup_gpu_pod to StepName enum check constraints.

The on-failure GPU pod cleanup step now has a formal StepName enum value
so its execution can be tracked in the workflow_steps table.

This migration updates the check constraints on:
  - workflow_steps.step_name  — add 'cleanup_gpu_pod'
  - workflows.current_step    — add 'cleanup_gpu_pod'

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op

revision: str = "0010"
down_revision: str = "0009"
branch_labels: None = None
depends_on: None = None

_OLD_STEP_NAMES = ["generate_story", "tts_synthesis", "image_generation", "stitch_final"]
_NEW_STEP_NAMES = _OLD_STEP_NAMES + ["cleanup_gpu_pod"]


def _enum_check_expr(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IS NULL OR {column} IN ({quoted})"


def upgrade() -> None:
    # workflow_steps.step_name
    op.drop_constraint("ck_workflow_steps_step_name", "workflow_steps", type_="check")
    op.create_check_constraint(
        "ck_workflow_steps_step_name",
        "workflow_steps",
        _enum_check_expr("step_name", _NEW_STEP_NAMES),
    )

    # workflows.current_step
    op.drop_constraint("ck_workflows_current_step", "workflows", type_="check")
    op.create_check_constraint(
        "ck_workflows_current_step",
        "workflows",
        _enum_check_expr("current_step", _NEW_STEP_NAMES),
    )


def downgrade() -> None:
    # workflows.current_step — restore old set
    op.drop_constraint("ck_workflows_current_step", "workflows", type_="check")
    op.create_check_constraint(
        "ck_workflows_current_step",
        "workflows",
        _enum_check_expr("current_step", _OLD_STEP_NAMES),
    )

    # workflow_steps.step_name — restore old set
    op.drop_constraint("ck_workflow_steps_step_name", "workflow_steps", type_="check")
    op.create_check_constraint(
        "ck_workflow_steps_step_name",
        "workflow_steps",
        _enum_check_expr("step_name", _OLD_STEP_NAMES),
    )
