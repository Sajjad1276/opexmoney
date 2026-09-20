"""add durable economy event outbox

Revision ID: 0018_economy_event_outbox
Revises: 0017_telegram_membership
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_economy_event_outbox"
down_revision = "0017_telegram_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economy_event_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_key", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", sa.BigInteger(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_until", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_key", name="uq_economy_event_outbox_event_key"),
    )
    op.create_index(
        "ix_economy_event_outbox_ready",
        "economy_event_outbox",
        ["published_at", "available_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_economy_event_outbox_ready",
        table_name="economy_event_outbox",
    )
    op.drop_table("economy_event_outbox")
