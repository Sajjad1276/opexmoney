"""Add Living Economy Protocol phase 2 tables.

Revision ID: 0003_living_economy_protocol
Revises: 0002_founder_constraints
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_living_economy_protocol"
down_revision = "0002_founder_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("proposer_player_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_key", sa.String(100), nullable=False),
        sa.Column("proposed_value", sa.Numeric(18, 6), nullable=False),
        sa.Column("target_scope", sa.String(20), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("voting_opens_at", sa.DateTime(), nullable=False),
        sa.Column("voting_closes_at", sa.DateTime(), nullable=False),
        sa.Column("effective_from", sa.DateTime(), nullable=True),
        sa.Column("effective_until", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_proposals_status_closing",
        "proposals",
        ["status", "voting_closes_at"],
    )
    op.create_index(
        "ix_proposals_proposer_created",
        "proposals",
        ["proposer_player_id", "created_at"],
    )

    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("choice", sa.String(10), nullable=False),
        sa.Column("weight", sa.Numeric(18, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("proposal_id", "player_id", name="uq_vote_proposal_player"),
    )
    op.create_index("ix_votes_proposal", "votes", ["proposal_id"])

    op.create_table(
        "rule_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_key", sa.String(100), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("source_proposal_id", sa.Integer(), sa.ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("active_from", sa.DateTime(), nullable=False),
        sa.Column("active_until", sa.DateTime(), nullable=True),
        sa.Column("suspended_until", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index(
        "ix_rule_overrides_rule_active",
        "rule_overrides",
        ["rule_key", "is_active"],
    )
    op.create_index(
        "uq_rule_override_active_global",
        "rule_overrides",
        ["rule_key"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE AND scope = 'global'"),
        sqlite_where=sa.text("is_active = 1 AND scope = 'global'"),
    )
    op.create_index(
        "uq_rule_override_active_target",
        "rule_overrides",
        ["rule_key", "scope", "target_id"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "governance_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("actor_player_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("rule_key", sa.String(100), nullable=False),
        sa.Column("old_value", sa.String(64), nullable=True),
        sa.Column("new_value", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
    )
    op.create_index("ix_governance_ledger_at", "governance_ledger", ["at"])

    op.create_table(
        "player_temporal_profiles",
        sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("peak_hour_start", sa.Integer(), nullable=False),
        sa.Column("peak_window_hours", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("peak_multiplier", sa.Numeric(8, 4), nullable=False, server_default=sa.text("1.5")),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("next_rotation_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_temporal_next_rotation",
        "player_temporal_profiles",
        ["next_rotation_at"],
    )

    op.create_table(
        "behavior_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("active_players_count", sa.Integer(), nullable=False),
        sa.Column("buy_tx_count", sa.Integer(), nullable=False),
        sa.Column("sell_tx_count", sa.Integer(), nullable=False),
        sa.Column("export_tx_count", sa.Integer(), nullable=False),
        sa.Column("import_tx_count", sa.Integer(), nullable=False),
        sa.Column("total_volume", sa.Numeric(20, 6), nullable=False),
        sa.Column("avg_net_worth", sa.Numeric(20, 6), nullable=False),
        sa.Column("median_net_worth", sa.Numeric(20, 6), nullable=False),
        sa.Column("gini_coefficient", sa.Numeric(10, 8), nullable=False),
        sa.Column("top10_wealth_share", sa.Numeric(10, 8), nullable=False),
    )
    op.create_index(
        "ix_behavior_snapshots_at",
        "behavior_snapshots",
        ["at"],
    )


def downgrade() -> None:
    op.drop_index("ix_behavior_snapshots_at", table_name="behavior_snapshots")
    op.drop_table("behavior_snapshots")

    op.drop_index("ix_temporal_next_rotation", table_name="player_temporal_profiles")
    op.drop_table("player_temporal_profiles")

    op.drop_index("ix_governance_ledger_at", table_name="governance_ledger")
    op.drop_table("governance_ledger")

    op.drop_index("uq_rule_override_active_target", table_name="rule_overrides")
    op.drop_index("uq_rule_override_active_global", table_name="rule_overrides")
    op.drop_index("ix_rule_overrides_rule_active", table_name="rule_overrides")
    op.drop_table("rule_overrides")

    op.drop_index("ix_votes_proposal", table_name="votes")
    op.drop_table("votes")

    op.drop_index("ix_proposals_proposer_created", table_name="proposals")
    op.drop_index("ix_proposals_status_closing", table_name="proposals")
    op.drop_table("proposals")
