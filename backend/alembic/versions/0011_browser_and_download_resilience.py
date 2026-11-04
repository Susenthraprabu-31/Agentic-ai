"""Browser sessions, document download attempts, and artifact resumption.

Revision ID: 0011_browser_download_resilience
Revises: 0010_worker_recovery
Create Date: 2026-11-02 12:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_browser_download_resilience"
down_revision: Union[str, None] = "0010_worker_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add columns to document_artifacts
    op.add_column("document_artifacts", sa.Column("etag", sa.String(length=255), nullable=True))
    op.add_column("document_artifacts", sa.Column("last_modified", sa.String(length=255), nullable=True))
    op.add_column("document_artifacts", sa.Column("downloaded_bytes", sa.Integer(), server_default="0", nullable=False))
    op.add_column("document_artifacts", sa.Column("download_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("document_artifacts", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("document_artifacts", sa.Column("temp_filepath", sa.String(length=1024), nullable=True))

    # 2. Create browser_sessions table
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("browser_type", sa.String(length=50), server_default="chromium", nullable=False),
        sa.Column("state", sa.String(length=50), server_default="READY", nullable=False),
        sa.Column("is_healthy", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("pages_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contexts_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_browser_sessions_session_id"), "browser_sessions", ["session_id"], unique=True)
    op.create_index(op.f("ix_browser_sessions_workflow_run_id"), "browser_sessions", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_browser_sessions_worker_id"), "browser_sessions", ["worker_id"], unique=False)
    op.create_index(op.f("ix_browser_sessions_state"), "browser_sessions", ["state"], unique=False)

    # 3. Create document_download_attempts table
    op.create_table(
        "document_download_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_artifact_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=50), server_default="in_progress", nullable=False),
        sa.Column("downloaded_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=True),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("range_resumed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("resumed_from_byte", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_artifact_id"], ["document_artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_download_attempts_document_artifact_id"), "document_download_attempts", ["document_artifact_id"], unique=False)
    op.create_index(op.f("ix_document_download_attempts_status"), "document_download_attempts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("document_download_attempts")
    op.drop_table("browser_sessions")
    op.drop_column("document_artifacts", "temp_filepath")
    op.drop_column("document_artifacts", "last_attempt_at")
    op.drop_column("document_artifacts", "download_attempts")
    op.drop_column("document_artifacts", "downloaded_bytes")
    op.drop_column("document_artifacts", "last_modified")
    op.drop_column("document_artifacts", "etag")

