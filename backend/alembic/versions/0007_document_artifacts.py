"""Document artifacts table.

Revision ID: 0007_document_artifacts
Revises: 0006_security_events
Create Date: 2026-10-31 17:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_document_artifacts"
down_revision: Union[str, None] = "0006_security_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_artifacts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("node_run_id", sa.Uuid(), nullable=True),
        sa.Column("property_record_id", sa.Uuid(), nullable=True),
        sa.Column("source_configuration_id", sa.Uuid(), nullable=True),
        sa.Column("document_type", sa.String(length=100), server_default="deed", nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_page_url", sa.String(length=2048), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=100), server_default="application/pdf", nullable=False),
        sa.Column("file_size", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("pdf_title", sa.String(length=255), nullable=True),
        sa.Column("pdf_author", sa.String(length=255), nullable=True),
        sa.Column("document_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_number", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_configuration_id"], ["source_configurations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["document_artifacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_artifacts_workflow_run_id"), "document_artifacts", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_document_artifacts_node_run_id"), "document_artifacts", ["node_run_id"], unique=False)
    op.create_index(op.f("ix_document_artifacts_property_record_id"), "document_artifacts", ["property_record_id"], unique=False)
    op.create_index(op.f("ix_document_artifacts_checksum_sha256"), "document_artifacts", ["checksum_sha256"], unique=False)
    op.create_index(op.f("ix_document_artifacts_recording_number"), "document_artifacts", ["recording_number"], unique=False)
    op.create_index(op.f("ix_document_artifacts_status"), "document_artifacts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_document_artifacts_status"), table_name="document_artifacts")
    op.drop_index(op.f("ix_document_artifacts_recording_number"), table_name="document_artifacts")
    op.drop_index(op.f("ix_document_artifacts_checksum_sha256"), table_name="document_artifacts")
    op.drop_index(op.f("ix_document_artifacts_property_record_id"), table_name="document_artifacts")
    op.drop_index(op.f("ix_document_artifacts_node_run_id"), table_name="document_artifacts")
    op.drop_index(op.f("ix_document_artifacts_workflow_run_id"), table_name="document_artifacts")
    op.drop_table("document_artifacts")

