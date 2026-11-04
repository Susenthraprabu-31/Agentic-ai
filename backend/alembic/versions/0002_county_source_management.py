"""County sources and source configurations tables.

Revision ID: 0002_county_source_management
Revises: 0001_initial_schema
Create Date: 2026-10-31 14:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_county_source_management"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # county_sources
    # -------------------------------------------------------------------------
    op.create_table(
        "county_sources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("county", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=50), server_default="US", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assessor_url", sa.String(length=2048), nullable=True),
        sa.Column("tax_url", sa.String(length=2048), nullable=True),
        sa.Column("assessor_status", sa.String(length=50), server_default="unknown", nullable=False),
        sa.Column("tax_status", sa.String(length=50), server_default="unknown", nullable=False),
        sa.Column("supported_search_types", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="active", nullable=False),
        sa.Column("config_status", sa.String(length=50), server_default="unconfigured", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_county_sources_state"), "county_sources", ["state"], unique=False)
    op.create_index(op.f("ix_county_sources_county"), "county_sources", ["county"], unique=False)
    op.create_index(op.f("ix_county_sources_created_by"), "county_sources", ["created_by"], unique=False)

    # -------------------------------------------------------------------------
    # ui_templates
    # -------------------------------------------------------------------------
    op.create_table(
        "ui_templates",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # -------------------------------------------------------------------------
    # template_versions
    # -------------------------------------------------------------------------
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["ui_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_template_versions_template_id"), "template_versions", ["template_id"], unique=False)

    # -------------------------------------------------------------------------
    # source_configurations
    # -------------------------------------------------------------------------
    op.create_table(
        "source_configurations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("county_source_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("website_url", sa.String(length=2048), nullable=False),
        sa.Column("search_page_url", sa.String(length=2048), nullable=True),
        sa.Column("search_method", sa.String(length=100), server_default="FORM_SUBMIT", nullable=True),
        sa.Column("search_fields", sa.JSON(), nullable=False),
        sa.Column("search_button", sa.JSON(), nullable=False),
        sa.Column("result_page", sa.JSON(), nullable=False),
        sa.Column("field_mappings", sa.JSON(), nullable=False),
        sa.Column("pagination_settings", sa.JSON(), nullable=False),
        sa.Column("navigation_config", sa.JSON(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="draft", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["county_source_id"], ["county_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_version_id"], ["template_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_source_configurations_county_source_id"), "source_configurations", ["county_source_id"], unique=False)
    op.create_index(op.f("ix_source_configurations_template_version_id"), "source_configurations", ["template_version_id"], unique=False)
    op.create_index(op.f("ix_source_configurations_created_by"), "source_configurations", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_table("source_configurations")
    op.drop_table("template_versions")
    op.drop_table("ui_templates")
    op.drop_table("county_sources")

