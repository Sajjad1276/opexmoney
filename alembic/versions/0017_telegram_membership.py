"""add Telegram membership projection

Revision ID: 0017_telegram_membership
Revises: 0016_ai_world

Stores Telegram group membership as the authoritative projection for
human-backed nations. It deliberately has no foreign key to users because
Telegram members may not have started the bot yet.
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_telegram_membership"
down_revision = "0016_ai_world"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nation_telegram_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_status", sa.String(length=20), nullable=False),
        sa.Column(
            "is_member",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.Column("left_at", sa.DateTime(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "nation_id",
            "telegram_user_id",
            name="uq_nation_telegram_member_nation_user",
        ),
    )
    op.create_index(
        "ix_nation_telegram_members_nation_active",
        "nation_telegram_members",
        ["nation_id", "is_active"],
    )
    op.create_index(
        "ix_nation_telegram_members_user_active",
        "nation_telegram_members",
        ["telegram_user_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nation_telegram_members_user_active",
        table_name="nation_telegram_members",
    )
    op.drop_index(
        "ix_nation_telegram_members_nation_active",
        table_name="nation_telegram_members",
    )
    op.drop_table("nation_telegram_members")
