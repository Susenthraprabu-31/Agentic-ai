"""Workflow and workflow version tables.

Revision ID: 0004_workflow_management
Revises: 0003_recipe_template_management
Create Date: 2026-10-31 15:35:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_workflow_management"
down_revision: Union[str, None] = "0003_recipe_template_management"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # workflows
    # -------------------------------------------------------------------------
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("county_source_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["county_source_id"], ["county_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflows_owner_id"), "workflows", ["owner_id"], unique=False)
    op.create_index(op.f("ix_workflows_county_source_id"), "workflows", ["county_source_id"], unique=False)

    # -------------------------------------------------------------------------
    # workflow_versions
    # -------------------------------------------------------------------------
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_versions_workflow_id"), "workflow_versions", ["workflow_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_versions_workflow_id"), table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_index(op.f("ix_workflows_county_source_id"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_owner_id"), table_name="workflows")
    op.drop_table("workflows")

