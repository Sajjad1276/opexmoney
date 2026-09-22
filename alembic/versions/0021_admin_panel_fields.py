"""admin panel fields for missions and player bans

Revision ID: 0021_admin_panel_fields
Revises: 0020_rate_history_factors
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_admin_panel_fields"
down_revision = "0020_rate_history_factors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "target_type",
            sa.String(length=20),
            nullable=False,
            server_default="custom",
        ),
    )
    op.add_column(
        "missions",
        sa.Column(
            "duration_days",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "missions",
        sa.Column(
            "reward_xp",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "ban_reason",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "ban_reason")
    op.drop_column("missions", "reward_xp")
    op.drop_column("missions", "duration_days")
    op.drop_column("missions", "target_type")
