"""Retry attempts table.

Revision ID: 0008_retry_attempts
Revises: 0007_document_artifacts
Create Date: 2026-11-02 09:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_retry_attempts"
down_revision: Union[str, None] = "0007_document_artifacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retry_attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("node_run_id", sa.Uuid(), nullable=True),
        sa.Column("operation_name", sa.String(length=100), nullable=False),
        sa.Column("operation_id", sa.String(length=100), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_category", sa.String(length=50), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_delay_seconds", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(length=50), server_default="retrying", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_retry_attempts_workflow_run_id"), "retry_attempts", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_retry_attempts_node_run_id"), "retry_attempts", ["node_run_id"], unique=False)
    op.create_index(op.f("ix_retry_attempts_operation_name"), "retry_attempts", ["operation_name"], unique=False)
    op.create_index(op.f("ix_retry_attempts_error_category"), "retry_attempts", ["error_category"], unique=False)
    op.create_index(op.f("ix_retry_attempts_status"), "retry_attempts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_retry_attempts_status"), table_name="retry_attempts")
    op.drop_index(op.f("ix_retry_attempts_error_category"), table_name="retry_attempts")
    op.drop_index(op.f("ix_retry_attempts_operation_name"), table_name="retry_attempts")
    op.drop_index(op.f("ix_retry_attempts_node_run_id"), table_name="retry_attempts")
    op.drop_index(op.f("ix_retry_attempts_workflow_run_id"), table_name="retry_attempts")
    op.drop_table("retry_attempts")

