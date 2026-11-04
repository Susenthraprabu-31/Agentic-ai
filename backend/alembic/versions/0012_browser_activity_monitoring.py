"""Browser activity monitoring columns.

Revision ID: 0012_browser_activity
Revises: 0011_browser_download_resilience
Create Date: 2026-11-02 13:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_browser_activity"
down_revision: Union[str, None] = "0011_browser_download_resilience"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("browser_sessions", sa.Column("current_url", sa.String(length=2048), nullable=True))
    op.add_column("browser_sessions", sa.Column("current_node", sa.String(length=100), nullable=True))
    op.add_column("browser_sessions", sa.Column("current_step", sa.String(length=255), nullable=True))
    op.add_column("browser_sessions", sa.Column("current_action", sa.String(length=100), nullable=True))
    op.add_column(
        "browser_sessions",
        sa.Column("activity_log", sa.JSON(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("browser_sessions", "activity_log")
    op.drop_column("browser_sessions", "current_action")
    op.drop_column("browser_sessions", "current_step")
    op.drop_column("browser_sessions", "current_node")
    op.drop_column("browser_sessions", "current_url")

