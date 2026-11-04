"""Execution engine tables (workflow_runs, node_runs, assessor_results, tax_results).

Revision ID: 0005_execution_engine
Revises: 0004_workflow_management
Create Date: 2026-10-31 15:53:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_execution_engine"
down_revision: Union[str, None] = "0004_workflow_management"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # workflow_runs
    # -------------------------------------------------------------------------
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("triggered_by", sa.Uuid(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="PENDING", nullable=False),
        sa.Column("input_data", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("execution_metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_runs_workflow_id"), "workflow_runs", ["workflow_id"], unique=False)
    op.create_index(op.f("ix_workflow_runs_workflow_version_id"), "workflow_runs", ["workflow_version_id"], unique=False)
    op.create_index(op.f("ix_workflow_runs_triggered_by"), "workflow_runs", ["triggered_by"], unique=False)
    op.create_index(op.f("ix_workflow_runs_status"), "workflow_runs", ["status"], unique=False)
    op.create_index(op.f("ix_workflow_runs_celery_task_id"), "workflow_runs", ["celery_task_id"], unique=False)

    # -------------------------------------------------------------------------
    # node_runs
    # -------------------------------------------------------------------------
    op.create_table(
        "node_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("node_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="PENDING", nullable=False),
        sa.Column("input_data", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("output_data", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("county_source_id", sa.Uuid(), nullable=True),
        sa.Column("recipe_version_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["county_source_id"], ["county_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recipe_version_id"], ["recipe_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_node_runs_workflow_run_id"), "node_runs", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_node_runs_status"), "node_runs", ["status"], unique=False)

    # -------------------------------------------------------------------------
    # assessor_results
    # -------------------------------------------------------------------------
    op.create_table(
        "assessor_results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("node_run_id", sa.Uuid(), nullable=False),
        sa.Column("parcel_number", sa.String(length=100), nullable=True),
        sa.Column("owner_name", sa.String(length=512), nullable=True),
        sa.Column("property_address", sa.Text(), nullable=True),
        sa.Column("legal_description", sa.Text(), nullable=True),
        sa.Column("assessed_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("land_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("improvement_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_year", sa.Integer(), nullable=True),
        sa.Column("raw_data", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("screenshot_s3_key", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assessor_results_node_run_id"), "assessor_results", ["node_run_id"], unique=True)

    # -------------------------------------------------------------------------
    # tax_results
    # -------------------------------------------------------------------------
    op.create_table(
        "tax_results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("node_run_id", sa.Uuid(), nullable=False),
        sa.Column("parcel_number", sa.String(length=100), nullable=True),
        sa.Column("tax_year", sa.Integer(), nullable=True),
        sa.Column("amount_due", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_paid", sa.Numeric(18, 2), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=100), nullable=True),
        sa.Column("raw_data", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("screenshot_s3_key", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tax_results_node_run_id"), "tax_results", ["node_run_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_tax_results_node_run_id"), table_name="tax_results")
    op.drop_table("tax_results")
    op.drop_index(op.f("ix_assessor_results_node_run_id"), table_name="assessor_results")
    op.drop_table("assessor_results")
    op.drop_index(op.f("ix_node_runs_status"), table_name="node_runs")
    op.drop_index(op.f("ix_node_runs_workflow_run_id"), table_name="node_runs")
    op.drop_table("node_runs")
    op.drop_index(op.f("ix_workflow_runs_celery_task_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_status"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_triggered_by"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_workflow_version_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_workflow_id"), table_name="workflow_runs")
    op.drop_table("workflow_runs")

