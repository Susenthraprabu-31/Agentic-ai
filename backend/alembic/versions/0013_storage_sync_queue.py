"""Add storage_sync_queue for S3 background uploads.

Revision ID: 0013_storage_sync_queue
Revises: 0012_browser_activity_monitoring
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_storage_sync_queue"
down_revision: Union[str, None] = "0012_browser_activity_monitoring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "storage_sync_queue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("local_path", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("s3_uri", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_storage_sync_queue_status"), "storage_sync_queue", ["status"], unique=False)
    op.create_index(op.f("ix_storage_sync_queue_storage_key"), "storage_sync_queue", ["storage_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_storage_sync_queue_storage_key"), table_name="storage_sync_queue")
    op.drop_index(op.f("ix_storage_sync_queue_status"), table_name="storage_sync_queue")
    op.drop_table("storage_sync_queue")