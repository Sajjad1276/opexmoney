"""Add market pressure factors and rate-history receipts.

Revision ID: 0020_rate_history_factors
Revises: 0019_foundation_v2
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_rate_history_factors"
down_revision = "0019_foundation_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rate_history",
        sa.Column("dominant_cause", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "rate_history",
        sa.Column(
            "pressure_signal",
            sa.Numeric(8, 6),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "rate_history",
        sa.Column(
            "foreign_signal",
            sa.Numeric(8, 6),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "rate_history",
        sa.Column(
            "activity_score",
            sa.Numeric(8, 6),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "rate_history",
        sa.Column(
            "trade_score",
            sa.Numeric(8, 6),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "rate_history",
        sa.Column(
            "growth_score",
            sa.Numeric(8, 6),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("rate_history", "growth_score")
    op.drop_column("rate_history", "trade_score")
    op.drop_column("rate_history", "activity_score")
    op.drop_column("rate_history", "foreign_signal")
    op.drop_column("rate_history", "pressure_signal")
    op.drop_column("rate_history", "dominant_cause")
