"""price alerts

Revision ID: 0011_price_alerts
Revises: 0010_academy
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_price_alerts"
down_revision = "0010_academy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(4),
            nullable=False,
        ),
        sa.Column(
            "target_price",
            sa.Numeric(18, 4),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.String(5),
            nullable=False,
            server_default=sa.text("'above'"),
        ),
        sa.Column(
            "triggered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "direction IN ('above', 'below')",
            name="ck_price_alert_direction",
        ),
    )

    op.create_index(
        "ix_price_alerts_user_created",
        "price_alerts",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_price_alerts_pending",
        "price_alerts",
        ["triggered", "currency_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_price_alerts_pending", table_name="price_alerts")
    op.drop_index("ix_price_alerts_user_created", table_name="price_alerts")
    op.drop_table("price_alerts")
