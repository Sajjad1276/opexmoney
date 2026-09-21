"""foundation v2 database layer

Revision ID: 0019_foundation_v2
Revises: 0018_market_query_indexes
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0019_foundation_v2"
down_revision = "0018_market_query_indexes"
branch_labels = None
depends_on = None


def _alter_existing_timestamp_columns(timezone_enabled: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    timestamp_columns = {
        "nations": ["last_rate_update", "created_at"],
        "bot_groups": ["created_at", "updated_at"],
        "users": ["created_at"],
        "currency_holdings": ["created_at"],
        "transactions": ["created_at"],
        "user_activities": ["created_at"],
        "nation_member_history": ["recorded_at"],
        "rate_history": ["calculated_at"],
        "trade_previews": ["created_at"],
        "nation_ranks": ["calculated_at"],
        "proposals": ["created_at", "voting_opens_at", "voting_closes_at", "effective_from", "effective_until"],
        "votes": ["created_at"],
        "rule_overrides": ["active_from", "active_until", "suspended_until"],
        "governance_ledger": ["at"],
        "player_temporal_profiles": ["assigned_at", "next_rotation_at"],
        "behavior_snapshots": ["at"],
        "nation_members": ["joined_at"],
        "nation_logs": ["created_at"],
        "nation_join_requests": ["created_at", "reviewed_at", "expires_at"],
        "nation_wars": ["declared_at", "ends_at", "ended_at"],
        "user_mission_progress": ["completed_at", "reset_at"],
        "nation_treasury": ["last_deposit_at"],
        "treasury_logs": ["created_at"],
        "user_lesson_progress": ["completed_at"],
        "user_xp": ["last_updated"],
        "nation_telegram_members": ["joined_at", "left_at", "last_seen_at"],
        "nation_founding_drafts": ["expires_at", "created_at", "updated_at"],
        "price_alerts": ["created_at"],
    }

    target_type = sa.DateTime(timezone=timezone_enabled)
    for table_name, columns in timestamp_columns.items():
        for column_name in columns:
            using = f'"{column_name}" AT TIME ZONE \'UTC\''
            op.alter_column(
                table_name,
                column_name,
                existing_type=sa.DateTime(timezone=not timezone_enabled),
                type_=target_type,
                postgresql_using=using,
            )


def upgrade() -> None:
    _alter_existing_timestamp_columns(True)

    op.add_column(
        "nations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_nations_active_deleted", "nations", ["is_active", "deleted_at"])

    op.add_column(
        "users",
        sa.Column("ai_tier", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE users SET ai_tier = 'bronze' WHERE ai_tier IS NULL")
    op.alter_column("users", "ai_tier", nullable=False, server_default="bronze")
    op.create_check_constraint(
        "ck_users_ai_tier",
        "users",
        "ai_tier IN ('bronze', 'silver', 'gold', 'diamond', 'elite')",
    )
    op.create_index("ix_users_telegram_id", "users", ["user_id"])
    op.create_index("ix_users_active_deleted", "users", ["deleted_at"])

    op.add_column(
        "nation_members",
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id
                    ORDER BY joined_at DESC, id DESC
                ) AS row_number
            FROM nation_members
            WHERE is_active = TRUE
        )
        UPDATE nation_members AS nm
        SET
            is_active = FALSE,
            left_at = CURRENT_TIMESTAMP
        FROM ranked
        WHERE nm.id = ranked.id
          AND ranked.row_number > 1
        """
    )

    op.create_index(
        "uq_nation_members_active_user",
        "nation_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )

    op.alter_column("nation_join_requests", "created_at", new_column_name="requested_at")
    op.alter_column("nation_join_requests", "reviewed_at", new_column_name="resolved_at")
    op.alter_column("nation_join_requests", "reviewed_by", new_column_name="resolved_by")
    op.create_check_constraint(
        "ck_nation_join_request_status",
        "nation_join_requests",
        "status IN ('pending', 'approved', 'rejected')",
    )
    op.create_index(
        "ix_nation_join_requests_requested_at",
        "nation_join_requests",
        ["requested_at"],
    )

    op.drop_index("ix_price_alerts_pending", table_name="price_alerts")
    op.alter_column("price_alerts", "triggered", new_column_name="is_triggered")
    op.add_column(
        "price_alerts",
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_price_alerts_pending",
        "price_alerts",
        ["is_triggered", "currency_code"],
    )
    op.create_index(
        "ix_price_alerts_user_triggered",
        "price_alerts",
        ["user_id", "is_triggered"],
    )

    op.create_table(
        "nation_invite_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "used_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("token", name="uq_nation_invite_link_token"),
        sa.CheckConstraint(
            "(is_used = FALSE) OR (used_by IS NOT NULL)",
            name="ck_nation_invite_link_used_consistency",
        ),
    )
    op.create_index(
        "ix_nation_invite_links_nation_expiry",
        "nation_invite_links",
        ["nation_id", "expires_at"],
    )
    op.create_index(
        "ix_nation_invite_links_unused_expiry",
        "nation_invite_links",
        ["is_used", "expires_at"],
    )

    op.create_table(
        "currency_market_states",
        sa.Column("currency_code", sa.String(length=4), primary_key=True),
        sa.Column("buy_pressure", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sell_pressure", sa.Float(), nullable=False, server_default="0"),
        sa.Column("liquidity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("volatility", sa.Float(), nullable=False, server_default="0"),
        sa.Column("foreign_demand", sa.Float(), nullable=False, server_default="0"),
        sa.Column("national_activity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("calculated_rate", sa.Numeric(18, 8), nullable=False, server_default="1.00000000"),
        sa.Column("previous_rate", sa.Numeric(18, 8), nullable=False, server_default="1.00000000"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "buy_pressure >= 0 AND buy_pressure <= 100",
            name="ck_market_buy_pressure",
        ),
        sa.CheckConstraint(
            "sell_pressure >= 0 AND sell_pressure <= 100",
            name="ck_market_sell_pressure",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_market_confidence",
        ),
        sa.CheckConstraint(
            "calculated_rate >= 0",
            name="ck_market_calculated_rate",
        ),
        sa.CheckConstraint(
            "previous_rate >= 0",
            name="ck_market_previous_rate",
        ),
    )
    op.create_index(
        "ix_currency_market_state_currency_code",
        "currency_market_states",
        ["currency_code"],
    )

    op.create_table(
        "price_movement_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("currency_code", sa.String(length=4), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rate_before", sa.Numeric(18, 8), nullable=False),
        sa.Column("rate_after", sa.Numeric(18, 8), nullable=False),
        sa.Column("change_percent", sa.Float(), nullable=False),
        sa.Column("cause_primary", sa.String(length=100), nullable=False),
        sa.Column(
            "cause_breakdown",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("triggered_by", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "triggered_by IN ('user_trade', 'ai_trade', 'scheduler', 'event', 'governance')",
            name="ck_price_receipt_triggered_by",
        ),
    )
    op.create_index(
        "ix_price_movement_receipts_currency_timestamp",
        "price_movement_receipts",
        ["currency_code", "timestamp"],
    )

    op.create_table(
        "world_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column(
            "affected_nation_id",
            sa.Integer(),
            sa.ForeignKey("nations.nation_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("affected_currency", sa.String(length=4), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("effect_type", sa.String(length=30), nullable=False),
        sa.Column("effect_magnitude", sa.Float(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "announced_in_group",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "duration_minutes > 0",
            name="ck_world_event_duration_positive",
        ),
    )
    op.create_index(
        "ix_world_events_active_ends_at",
        "world_events",
        ["is_active", "ends_at"],
    )
    op.create_index(
        "ix_world_events_nation_active",
        "world_events",
        ["affected_nation_id", "is_active"],
    )

    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("advisor_questions_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("portfolio_scans_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("war_analysis_used", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "date", name="uq_ai_usage_user_date"),
    )
    op.create_index("ix_ai_usage_logs_user_date", "ai_usage_logs", ["user_id", "date"])

    op.create_table(
        "decision_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("currency_code", sa.String(length=4), nullable=False),
        sa.Column("amount", sa.Numeric(18, 8), nullable=False),
        sa.Column("rate_at_decision", sa.Numeric(18, 8), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_outcome", sa.Numeric(18, 8), nullable=True),
        sa.Column("alternative_outcome", sa.Numeric(18, 8), nullable=True),
        sa.Column("opportunity_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "is_evaluated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.UniqueConstraint(
            "transaction_id",
            name="uq_decision_snapshot_transaction",
        ),
        sa.CheckConstraint(
            "action IN ('buy', 'sell', 'hold')",
            name="ck_decision_snapshot_action",
        ),
    )
    op.create_index(
        "ix_decision_snapshots_user_evaluated",
        "decision_snapshots",
        ["user_id", "is_evaluated"],
    )

    op.create_table(
        "shop_items",
        sa.Column("item_key", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_opx", sa.Integer(), nullable=True),
        sa.Column("price_stars", sa.Integer(), nullable=True),
        sa.Column("duration_hours", sa.Integer(), nullable=True),
        sa.Column("required_tier", sa.String(length=10), nullable=False, server_default="bronze"),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("item_key", name="uq_shop_item_key"),
        sa.CheckConstraint(
            "required_tier IN ('bronze', 'silver', 'gold', 'diamond', 'elite')",
            name="ck_shop_item_required_tier",
        ),
        sa.CheckConstraint(
            "category IN ('boost', 'permanent', 'subscription')",
            name="ck_shop_item_category",
        ),
    )
    op.create_index(
        "ix_shop_items_active_category",
        "shop_items",
        ["is_active", "category"],
    )
    op.create_index(
        "ix_shop_items_required_tier",
        "shop_items",
        ["required_tier"],
    )

    op.create_table(
        "user_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_key",
            sa.String(length=80),
            sa.ForeignKey("shop_items.item_key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchased_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("payment_method", sa.String(length=10), nullable=False),
        sa.CheckConstraint(
            "payment_method IN ('opx', 'stars', 'promo')",
            name="ck_user_purchase_payment_method",
        ),
    )
    op.create_index(
        "ix_user_purchases_user_active",
        "user_purchases",
        ["user_id", "is_active"],
    )
    op.create_index(
        "ix_user_purchases_expires_at",
        "user_purchases",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_purchases_expires_at", table_name="user_purchases")
    op.drop_index("ix_user_purchases_user_active", table_name="user_purchases")
    op.drop_table("user_purchases")

    op.drop_index("ix_shop_items_required_tier", table_name="shop_items")
    op.drop_index("ix_shop_items_active_category", table_name="shop_items")
    op.drop_table("shop_items")

    op.drop_index("ix_decision_snapshots_user_evaluated", table_name="decision_snapshots")
    op.drop_table("decision_snapshots")

    op.drop_index("ix_ai_usage_logs_user_date", table_name="ai_usage_logs")
    op.drop_table("ai_usage_logs")

    op.drop_index("ix_world_events_nation_active", table_name="world_events")
    op.drop_index("ix_world_events_active_ends_at", table_name="world_events")
    op.drop_table("world_events")

    op.drop_index(
        "ix_price_movement_receipts_currency_timestamp",
        table_name="price_movement_receipts",
    )
    op.drop_table("price_movement_receipts")

    op.drop_index(
        "ix_currency_market_state_currency_code",
        table_name="currency_market_states",
    )
    op.drop_table("currency_market_states")

    op.drop_index(
        "ix_nation_invite_links_unused_expiry",
        table_name="nation_invite_links",
    )
    op.drop_index(
        "ix_nation_invite_links_nation_expiry",
        table_name="nation_invite_links",
    )
    op.drop_table("nation_invite_links")

    op.drop_index("ix_price_alerts_user_triggered", table_name="price_alerts")
    op.drop_index("ix_price_alerts_pending", table_name="price_alerts")
    op.drop_column("price_alerts", "triggered_at")
    op.alter_column("price_alerts", "is_triggered", new_column_name="triggered")
    op.create_index(
        "ix_price_alerts_pending",
        "price_alerts",
        ["triggered", "currency_code"],
    )

    op.drop_index(
        "ix_nation_join_requests_requested_at",
        table_name="nation_join_requests",
    )
    op.drop_constraint(
        "ck_nation_join_request_status",
        "nation_join_requests",
        type_="check",
    )
    op.alter_column("nation_join_requests", "resolved_by", new_column_name="reviewed_by")
    op.alter_column("nation_join_requests", "resolved_at", new_column_name="reviewed_at")
    op.alter_column("nation_join_requests", "requested_at", new_column_name="created_at")

    op.drop_index("uq_nation_members_active_user", table_name="nation_members")
    op.drop_column("nation_members", "left_at")

    op.drop_constraint("ck_users_ai_tier", "users", type_="check")
    op.drop_index("ix_users_active_deleted", table_name="users")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "ai_tier")

    op.drop_index("ix_nations_active_deleted", table_name="nations")
    op.drop_column("nations", "deleted_at")

    _alter_existing_timestamp_columns(False)
