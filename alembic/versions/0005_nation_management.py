"""Add nation hierarchy, join workflow, activity logs and nation management settings.

Revision ID: 0005_nation_management
Revises: 0004_onboarding_username_length
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_nation_management"
down_revision = "0004_onboarding_username_length"
branch_labels = None
depends_on = None

nation_member_role = postgresql.ENUM(
    "founder",
    "minister",
    "trader",
    "citizen",
    name="nation_member_role",
)


def upgrade() -> None:
    nation_member_role.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "nations",
        sa.Column("join_policy", sa.String(20), nullable=True, server_default=sa.text("'OPEN'")),
    )
    op.add_column(
        "nations",
        sa.Column("personality", sa.String(20), nullable=True, server_default=sa.text("'neutral'")),
    )
    op.add_column(
        "nations",
        sa.Column("invite_code", sa.String(64), nullable=True),
    )
    op.add_column(
        "nations",
        sa.Column("treasury", sa.Numeric(20, 2), nullable=True, server_default=sa.text("0.00")),
    )

    op.execute("UPDATE nations SET join_policy = 'OPEN' WHERE join_policy IS NULL")
    op.execute("UPDATE nations SET personality = 'neutral' WHERE personality IS NULL")
    op.execute(
        "UPDATE nations "
        "SET invite_code = 'OPX-' || nation_id::text || '-JOIN' "
        "WHERE invite_code IS NULL"
    )
    op.execute("UPDATE nations SET treasury = 0 WHERE treasury IS NULL")

    op.alter_column("nations", "join_policy", nullable=False, server_default=None)
    op.alter_column("nations", "personality", nullable=False, server_default=None)
    op.alter_column("nations", "invite_code", nullable=False)
    op.alter_column("nations", "treasury", nullable=False, server_default=None)
    op.create_index("uq_nations_invite_code", "nations", ["invite_code"], unique=True)

    op.create_table(
        "nation_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", nation_member_role, nullable=False, server_default=sa.text("'citizen'")),
        sa.Column("joined_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("nation_id", "user_id", name="uq_nation_member_nation_user"),
    )
    op.create_index(
        "ix_nation_members_nation_role",
        "nation_members",
        ["nation_id", "role"],
    )
    op.create_index(
        "ix_nation_members_user_active",
        "nation_members",
        ["user_id", "is_active"],
    )

    op.execute(
        """
        INSERT INTO nation_members (nation_id, user_id, role, joined_at, is_active)
        SELECT nation_id, founder_user_id, 'founder', COALESCE(created_at, CURRENT_TIMESTAMP), true
        FROM nations
        WHERE founder_user_id IS NOT NULL
        ON CONFLICT (nation_id, user_id) DO NOTHING
        """
    )

    op.create_table(
        "nation_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(40), nullable=False),
        sa.Column(
            "target_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index(
        "ix_nation_logs_nation_created",
        "nation_logs",
        ["nation_id", "created_at"],
    )
    op.create_index(
        "ix_nation_logs_nation_action",
        "nation_logs",
        ["nation_id", "action_type"],
    )

    op.execute(
        """
        INSERT INTO nation_logs (nation_id, actor_id, action_type, target_id, metadata)
        SELECT nation_id, founder_user_id, 'MEMBER_JOIN', founder_user_id,
               '{"role": "founder", "source": "nation_creation"}'::jsonb
        FROM nations
        WHERE founder_user_id IS NOT NULL
        """
    )

    op.create_table(
        "nation_join_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "reviewed_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uq_nation_join_request_pending",
        "nation_join_requests",
        ["nation_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_nation_join_requests_expires",
        "nation_join_requests",
        ["status", "expires_at"],
    )

    op.create_table(
        "nation_wars",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opponent_nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("declared_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_nation_wars_nation_status",
        "nation_wars",
        ["nation_id", "status"],
    )
    op.create_index(
        "ix_nation_wars_opponent_status",
        "nation_wars",
        ["opponent_nation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_nation_wars_opponent_status", table_name="nation_wars")
    op.drop_index("ix_nation_wars_nation_status", table_name="nation_wars")
    op.drop_table("nation_wars")

    op.drop_index("ix_nation_join_requests_expires", table_name="nation_join_requests")
    op.drop_index("uq_nation_join_request_pending", table_name="nation_join_requests")
    op.drop_table("nation_join_requests")

    op.drop_index("ix_nation_logs_nation_action", table_name="nation_logs")
    op.drop_index("ix_nation_logs_nation_created", table_name="nation_logs")
    op.drop_table("nation_logs")

    op.drop_index("ix_nation_members_user_active", table_name="nation_members")
    op.drop_index("ix_nation_members_nation_role", table_name="nation_members")
    op.drop_table("nation_members")

    op.drop_index("uq_nations_invite_code", table_name="nations")
    op.drop_column("nations", "treasury")
    op.drop_column("nations", "invite_code")
    op.drop_column("nations", "personality")
    op.drop_column("nations", "join_policy")

    nation_member_role.drop(op.get_bind(), checkfirst=True)
