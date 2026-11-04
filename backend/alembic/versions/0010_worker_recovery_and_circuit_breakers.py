"""Worker records, circuit breaker records, recovery audit, and workflow run fencing.

Revision ID: 0010_worker_recovery
Revises: 0009_idempotency_records
Create Date: 2026-11-02 10:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_worker_recovery"
down_revision: Union[str, None] = "0009_idempotency_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add recovery & fencing columns to workflow_runs
    op.add_column("workflow_runs", sa.Column("worker_id", sa.String(length=255), nullable=True))
    op.add_column("workflow_runs", sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False))
    op.add_column("workflow_runs", sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflow_runs", sa.Column("recovery_attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("workflow_runs", sa.Column("last_recovery_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflow_runs", sa.Column("recovery_reason", sa.Text(), nullable=True))
    op.add_column("workflow_runs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_workflow_runs_worker_id"), "workflow_runs", ["worker_id"], unique=False)

    # 2. Create worker_records table
    op.create_table(
        "worker_records",
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("queues", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="ONLINE", nullable=False),
        sa.Column("active_task_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_task_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(op.f("ix_worker_records_status"), "worker_records", ["status"], unique=False)
    op.create_index(op.f("ix_worker_records_last_heartbeat_at"), "worker_records", ["last_heartbeat_at"], unique=False)

    # 3. Create circuit_breaker_records table
    op.create_table(
        "circuit_breaker_records",
        sa.Column("dependency_key", sa.String(length=255), nullable=False),
        sa.Column("dependency_type", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=50), server_default="CLOSED", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consecutive_successes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_state_change_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_threshold", sa.Integer(), server_default="5", nullable=False),
        sa.Column("open_duration_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("half_open_max_probes", sa.Integer(), server_default="2", nullable=False),
        sa.Column("half_open_active_probes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("circuit_metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("dependency_key"),
    )
    op.create_index(op.f("ix_circuit_breaker_records_dependency_type"), "circuit_breaker_records", ["dependency_type"], unique=False)
    op.create_index(op.f("ix_circuit_breaker_records_state"), "circuit_breaker_records", ["state"], unique=False)

    # 4. Create workflow_recovery_records table
    op.create_table(
        "workflow_recovery_records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("previous_worker_id", sa.String(length=255), nullable=True),
        sa.Column("recovery_worker_id", sa.String(length=255), nullable=True),
        sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=50), server_default="STARTED", nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_recovery_records_workflow_run_id"), "workflow_recovery_records", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_workflow_recovery_records_status"), "workflow_recovery_records", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("workflow_recovery_records")
    op.drop_table("circuit_breaker_records")
    op.drop_table("worker_records")
    op.drop_index(op.f("ix_workflow_runs_worker_id"), table_name="workflow_runs")
    op.drop_column("workflow_runs", "last_heartbeat_at")
    op.drop_column("workflow_runs", "recovery_reason")
    op.drop_column("workflow_runs", "last_recovery_at")
    op.drop_column("workflow_runs", "recovery_attempt_count")
    op.drop_column("workflow_runs", "worker_lease_expires_at")
    op.drop_column("workflow_runs", "fencing_token")
    op.drop_column("workflow_runs", "worker_id")

