"""Recipe and template management tables.

Revision ID: 0003_recipe_template_management
Revises: 0002_county_source_management
Create Date: 2026-10-31 15:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_recipe_template_management"
down_revision: Union[str, None] = "0002_county_source_management"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # recipes
    # -------------------------------------------------------------------------
    op.create_table(
        "recipes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("county_source_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("ui_template_id", sa.Uuid(), nullable=True),
        sa.Column("source_configuration_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="draft", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["county_source_id"], ["county_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ui_template_id"], ["ui_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_configuration_id"], ["source_configurations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recipes_county_source_id"), "recipes", ["county_source_id"], unique=False)
    op.create_index(op.f("ix_recipes_ui_template_id"), "recipes", ["ui_template_id"], unique=False)
    op.create_index(op.f("ix_recipes_source_configuration_id"), "recipes", ["source_configuration_id"], unique=False)

    # -------------------------------------------------------------------------
    # recipe_versions
    # -------------------------------------------------------------------------
    op.create_table(
        "recipe_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=50), server_default="draft", nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("validation_status", sa.String(length=50), server_default="untested", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_version_id"], ["template_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recipe_versions_recipe_id"), "recipe_versions", ["recipe_id"], unique=False)

    # -------------------------------------------------------------------------
    # validation_cases
    # -------------------------------------------------------------------------
    op.create_table(
        "validation_cases",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), server_default="Default Validation Test", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("expected_output", sa.JSON(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_passed", sa.Boolean(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_validation_cases_recipe_id"), "validation_cases", ["recipe_id"], unique=False)


def downgrade() -> None:
    op.drop_table("validation_cases")
    op.drop_table("recipe_versions")
    op.drop_table("recipes")

