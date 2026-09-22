"""add active state to admin price alerts

Revision ID: 0022_admin_price_alert_active
Revises: 0021_admin_panel_fields
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_admin_price_alert_active"
down_revision = "0021_admin_panel_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "price_alerts",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "price_alerts",
        sa.Column("triggered_value", sa.Numeric(18, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("price_alerts", "triggered_value")
    op.drop_column("price_alerts", "is_active")
