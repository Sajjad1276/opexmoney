"""Create the current OPEX MONEY base schema.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

activity_type = postgresql.ENUM("trade", "login", "mission", name="activity_type")


def upgrade() -> None:
    bind = op.get_bind()
    activity_type.create(bind, checkfirst=True)
    op.create_table("nations",
        sa.Column("nation_id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.BigInteger()),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("currency_code", sa.String(4), nullable=False),
        sa.Column("founder_user_id", sa.BigInteger()),
        sa.Column("exchange_rate", sa.Numeric(12,4), nullable=False, server_default=sa.text("1.0000")),
        sa.Column("rate_prev", sa.Numeric(10,4), nullable=False, server_default=sa.text("1.0000")),
        sa.Column("rate_24h_open", sa.Numeric(10,4), nullable=False, server_default=sa.text("1.0000")),
        sa.Column("trade_volume_24h", sa.Numeric(18,4), nullable=False, server_default=sa.text("0")),
        sa.Column("active_members_24h", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("nation_rank", sa.Integer()),
        sa.Column("last_rate_update", sa.DateTime()),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table("users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(15), nullable=False),
        sa.Column("home_nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id")),
        sa.Column("balance", sa.Numeric(14,2), nullable=False, server_default=sa.text("500.00")),
        sa.Column("xr_balance", sa.Numeric(14,2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("role", sa.String(20), nullable=False, server_default=sa.text("'player'")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table("currency_holdings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(18,4), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id","nation_id",name="uq_currency_holding_user_nation"),
    )
    op.create_table("transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id"), nullable=False),
        sa.Column("transaction_type", sa.String(10), nullable=False),
        sa.Column("spend_xr", sa.Numeric(18,4), nullable=False),
        sa.Column("amount", sa.Numeric(18,4), nullable=False),
        sa.Column("fee_xr", sa.Numeric(18,4), nullable=False),
        sa.Column("rate", sa.Numeric(10,4), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table("user_activities",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id"), nullable=False),
        sa.Column("activity_type", activity_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table("nation_member_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table("rate_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rate", sa.Numeric(10,4), nullable=False),
        sa.Column("volume", sa.Numeric(18,4), nullable=False),
        sa.Column("active_members", sa.Integer(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table("trade_previews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("spend", sa.Numeric(18,4), nullable=False),
        sa.Column("preview_rate", sa.Numeric(10,4), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table("nation_ranks",
        sa.Column("nation_id", sa.Integer(), sa.ForeignKey("nations.nation_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_transactions_user_created","transactions",["user_id","created_at"])
    op.create_index("ix_user_activities_nation_created","user_activities",["nation_id","created_at"])
    op.create_index("ix_nation_member_history_nation_recorded","nation_member_history",["nation_id","recorded_at"])
    op.create_index("ix_rate_history_nation_calculated","rate_history",["nation_id","calculated_at"])
    op.create_index("ix_trade_previews_user_created","trade_previews",["user_id","created_at"])


def downgrade() -> None:
    for index, table in [
        ("ix_trade_previews_user_created","trade_previews"),
        ("ix_rate_history_nation_calculated","rate_history"),
        ("ix_nation_member_history_nation_recorded","nation_member_history"),
        ("ix_user_activities_nation_created","user_activities"),
        ("ix_transactions_user_created","transactions"),
    ]:
        op.drop_index(index, table_name=table)
    for table in ["nation_ranks","trade_previews","rate_history","nation_member_history","user_activities","transactions","currency_holdings","users","nations"]:
        op.drop_table(table)
    activity_type.drop(op.get_bind(), checkfirst=True)
