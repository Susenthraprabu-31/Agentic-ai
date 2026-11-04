"""Security events table.

Revision ID: 0006_security_events
Revises: 0005_execution_engine
Create Date: 2026-10-31 16:06:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_security_events"
down_revision: Union[str, None] = "0005_execution_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("county_source_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["county_source_id"], ["county_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_security_events_workflow_run_id"), "security_events", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_security_events_event_type"), "security_events", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_security_events_event_type"), table_name="security_events")
    op.drop_index(op.f("ix_security_events_workflow_run_id"), table_name="security_events")
    op.drop_table("security_events")

