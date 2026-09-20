"""add durable nation economic events

Revision ID: 0018_economic_events
Revises: 0017_telegram_membership
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018_economic_events"
down_revision = "0017_telegram_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nation_economic_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "actor_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "amount_xr",
            sa.Numeric(20, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "amount_local",
            sa.Numeric(20, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_nation_economic_events_nation_created",
        "nation_economic_events",
        ["nation_id", "created_at"],
    )
    op.create_index(
        "ix_nation_economic_events_nation_type_created",
        "nation_economic_events",
        ["nation_id", "event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nation_economic_events_nation_type_created",
        table_name="nation_economic_events",
    )
    op.drop_index(
        "ix_nation_economic_events_nation_created",
        table_name="nation_economic_events",
    )
    op.drop_table("nation_economic_events")
