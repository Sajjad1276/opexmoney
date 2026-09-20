"""add persistent nation founding drafts

Revision ID: 0015_nation_founding_drafts
Revises: 0014_nation_war_flow

Stores the founder wizard in the database so the Telegram FSM is only a UI
state. This prevents group discovery and partial registration from depending on
an in-memory message flow.
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_nation_founding_drafts"
down_revision = "0014_nation_war_flow"
branch_labels = None
depends_on = None


_ACTIVE = (
    "status IN ('WAITING_GROUP', 'GROUP_READY', 'NAMING', "
    "'FLAG', 'REVIEW', 'FINALIZING')"
)


def upgrade() -> None:
    op.create_table(
        "nation_founding_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "founder_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("launch_token", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("group_title", sa.String(length=255), nullable=True),
        sa.Column("group_username", sa.String(length=255), nullable=True),
        sa.Column("group_type", sa.String(length=20), nullable=True),
        sa.Column("nation_name", sa.String(length=100), nullable=True),
        sa.Column("currency_code", sa.String(length=4), nullable=True),
        sa.Column(
            "flag_emoji",
            sa.String(length=10),
            nullable=False,
            server_default="🏴",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="WAITING_GROUP",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "launch_token",
            name="uq_nation_founding_draft_token",
        ),
    )

    op.create_index(
        "uq_nation_founding_draft_founder_active",
        "nation_founding_drafts",
        ["founder_user_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        sqlite_where=sa.text(_ACTIVE.replace("TRUE", "1")),
    )
    op.create_index(
        "uq_nation_founding_draft_group_active",
        "nation_founding_drafts",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text(
            "group_id IS NOT NULL AND " + _ACTIVE
        ),
        sqlite_where=sa.text(
            "group_id IS NOT NULL AND " + _ACTIVE.replace("TRUE", "1")
        ),
    )
    op.create_index(
        "ix_nation_founding_drafts_expiry",
        "nation_founding_drafts",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nation_founding_drafts_expiry",
        table_name="nation_founding_drafts",
    )
    op.drop_index(
        "uq_nation_founding_draft_group_active",
        table_name="nation_founding_drafts",
    )
    op.drop_index(
        "uq_nation_founding_draft_founder_active",
        table_name="nation_founding_drafts",
    )
    op.drop_table("nation_founding_drafts")
