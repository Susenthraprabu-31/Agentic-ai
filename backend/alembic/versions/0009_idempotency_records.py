"""Idempotency records table.

Revision ID: 0009_idempotency_records
Revises: 0008_retry_attempts
Create Date: 2026-11-02 09:35:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_idempotency_records"
down_revision: Union[str, None] = "0008_retry_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("request_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("node_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="processing", nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key_hash", "operation_type", name="uq_idempotency_user_key_op"),
    )
    op.create_index(op.f("ix_idempotency_records_user_id"), "idempotency_records", ["user_id"], unique=False)
    op.create_index(op.f("ix_idempotency_records_idempotency_key_hash"), "idempotency_records", ["idempotency_key_hash"], unique=False)
    op.create_index(op.f("ix_idempotency_records_operation_type"), "idempotency_records", ["operation_type"], unique=False)
    op.create_index(op.f("ix_idempotency_records_resource_id"), "idempotency_records", ["resource_id"], unique=False)
    op.create_index(op.f("ix_idempotency_records_workflow_run_id"), "idempotency_records", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_idempotency_records_node_run_id"), "idempotency_records", ["node_run_id"], unique=False)
    op.create_index(op.f("ix_idempotency_records_status"), "idempotency_records", ["status"], unique=False)
    op.create_index(op.f("ix_idempotency_records_expires_at"), "idempotency_records", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_idempotency_records_expires_at"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_status"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_node_run_id"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_workflow_run_id"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_resource_id"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_operation_type"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_idempotency_key_hash"), table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_user_id"), table_name="idempotency_records")
    op.drop_table("idempotency_records")

