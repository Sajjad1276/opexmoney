from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.dialects import postgresql
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ActivityType(StrEnum):
    TRADE = "trade"
    LOGIN = "login"
    MISSION = "mission"


class Nation(Base):
    __tablename__ = "nations"
    __table_args__ = (
        Index("uq_nations_currency_code", "currency_code", unique=True),
        Index(
            "uq_nations_active_group",
            "group_id",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
        ),
    )
    nation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Telegram chat/user identifiers can exceed PostgreSQL INTEGER (int4).
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    flag_emoji: Mapped[str | None] = mapped_column(String(10), nullable=True, default="🏴")
    currency_code: Mapped[str] = mapped_column(String(4))
    founder_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    rate_prev: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1.0000"), nullable=False)
    rate_24h_open: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1.0000"), nullable=False)
    trade_volume_24h: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    active_members_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nation_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_rate_update: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    join_policy: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    personality: Mapped[str] = mapped_column(String(20), default="neutral", nullable=False)
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    invite_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    treasury: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0.00"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BotGroup(Base):
    __tablename__ = "bot_groups"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NationTelegramMember(Base):
    __tablename__ = "nation_telegram_members"
    __table_args__ = (
        UniqueConstraint(
            "nation_id",
            "telegram_user_id",
            name="uq_nation_telegram_member_nation_user",
        ),
        Index(
            "ix_nation_telegram_members_nation_active",
            "nation_id",
            "is_active",
        ),
        Index(
            "ix_nation_telegram_members_user_active",
            "telegram_user_id",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )


class NationFoundingDraft(Base):
    __tablename__ = "nation_founding_drafts"
    __table_args__ = (
        UniqueConstraint("launch_token", name="uq_nation_founding_draft_token"),
        Index(
            "uq_nation_founding_draft_founder_active",
            "founder_user_id",
            unique=True,
            postgresql_where=text(
                "status IN ('WAITING_GROUP', 'GROUP_READY', 'NAMING', 'FLAG', 'REVIEW', 'FINALIZING')"
            ),
            sqlite_where=text(
                "status IN ('WAITING_GROUP', 'GROUP_READY', 'NAMING', 'FLAG', 'REVIEW', 'FINALIZING')"
            ),
        ),
        Index(
            "uq_nation_founding_draft_group_active",
            "group_id",
            unique=True,
            postgresql_where=text(
                "group_id IS NOT NULL AND status IN ('WAITING_GROUP', 'GROUP_READY', 'NAMING', 'FLAG', 'REVIEW', 'FINALIZING')"
            ),
            sqlite_where=text(
                "group_id IS NOT NULL AND status IN ('WAITING_GROUP', 'GROUP_READY', 'NAMING', 'FLAG', 'REVIEW', 'FINALIZING')"
            ),
        ),
        Index(
            "ix_nation_founding_drafts_expiry",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    founder_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    launch_token: Mapped[str] = mapped_column(String(64), nullable=False)
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    group_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nation_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    flag_emoji: Mapped[str] = mapped_column(String(10), default="🏴", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default="WAITING_GROUP",
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(20), nullable=False)
    home_nation_id: Mapped[int | None] = mapped_column(ForeignKey("nations.nation_id"))
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("500.00"), nullable=False)
    xr_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="player", nullable=False)
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_strategy: Mapped[str] = mapped_column(String(20), default="balanced", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CurrencyHolding(Base):
    __tablename__ = "currency_holdings"
    __table_args__ = (UniqueConstraint("user_id", "nation_id", name="uq_currency_holding_user_nation"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_user_created", "user_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id"), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    spend_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    fee_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PriceAlert(Base):
    __tablename__ = "price_alerts"
    __table_args__ = (
        Index("ix_price_alerts_user_created", "user_id", "created_at"),
        Index("ix_price_alerts_pending", "triggered", "currency_code"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(String(4), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False, default="above")
    triggered: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserActivity(Base):
    __tablename__ = "user_activities"
    __table_args__ = (Index("ix_user_activities_nation_created", "nation_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id"), nullable=False)
    activity_type: Mapped[ActivityType] = mapped_column(
        SAEnum(ActivityType, name="activity_type", values_callable=lambda values: [item.value for item in values]),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NationMemberHistory(Base):
    __tablename__ = "nation_member_history"
    __table_args__ = (Index("ix_nation_member_history_nation_recorded", "nation_id", "recorded_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RateHistory(Base):
    __tablename__ = "rate_history"
    __table_args__ = (Index("ix_rate_history_nation_calculated", "nation_id", "calculated_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    active_members: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TradePreview(Base):
    __tablename__ = "trade_previews"
    __table_args__ = (Index("ix_trade_previews_user_created", "user_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    preview_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NationRank(Base):
    __tablename__ = "nation_ranks"
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        Index("ix_proposals_status_closing", "status", "voting_closes_at"),
        Index("ix_proposals_proposer_created", "proposer_player_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    proposer_player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    proposed_value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    voting_opens_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    voting_closes_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("proposal_id", "player_id", name="uq_vote_proposal_player"),
        Index("ix_votes_proposal", "proposal_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(Integer, ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    choice: Mapped[str] = mapped_column(String(10), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RuleOverride(Base):
    __tablename__ = "rule_overrides"
    __table_args__ = (
        Index("ix_rule_overrides_rule_active", "rule_key", "is_active"),
        Index(
            "uq_rule_override_active_global",
            "rule_key",
            unique=True,
            postgresql_where=text("is_active = TRUE AND scope = 'global'"),
            sqlite_where=text("is_active = 1 AND scope = 'global'"),
        ),
        Index(
            "uq_rule_override_active_target",
            "rule_key",
            "scope",
            "target_id",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
            sqlite_where=text("is_active = 1"),
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_proposal_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True)
    active_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    suspended_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class GovernanceLedger(Base):
    __tablename__ = "governance_ledger"
    __table_args__ = (Index("ix_governance_ledger_at", "at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actor_player_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PlayerTemporalProfile(Base):
    __tablename__ = "player_temporal_profiles"
    __table_args__ = (Index("ix_temporal_next_rotation", "next_rotation_at"),)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    peak_hour_start: Mapped[int] = mapped_column(Integer, nullable=False)
    peak_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    peak_multiplier: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("1.5"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    next_rotation_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BehaviorSnapshot(Base):
    __tablename__ = "behavior_snapshots"
    __table_args__ = (Index("ix_behavior_snapshots_at", "at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active_players_count: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_tx_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_tx_count: Mapped[int] = mapped_column(Integer, nullable=False)
    export_tx_count: Mapped[int] = mapped_column(Integer, nullable=False)
    import_tx_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_volume: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    avg_net_worth: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    median_net_worth: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    gini_coefficient: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    top10_wealth_share: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)


class NationMemberRole(StrEnum):
    FOUNDER = "founder"
    MINISTER = "minister"
    TRADER = "trader"
    CITIZEN = "citizen"


class NationMember(Base):
    __tablename__ = "nation_members"
    __table_args__ = (
        UniqueConstraint("nation_id", "user_id", name="uq_nation_member_nation_user"),
        Index("ix_nation_members_nation_role", "nation_id", "role"),
        Index("ix_nation_members_user_active", "user_id", "is_active"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[NationMemberRole] = mapped_column(
        SAEnum(
            NationMemberRole,
            name="nation_member_role",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=NationMemberRole.CITIZEN,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class NationLog(Base):
    __tablename__ = "nation_logs"
    __table_args__ = (
        Index("ix_nation_logs_nation_created", "nation_id", "created_at"),
        Index("ix_nation_logs_nation_action", "nation_id", "action_type"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        postgresql.JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NationJoinRequest(Base):
    __tablename__ = "nation_join_requests"
    __table_args__ = (
        Index(
            "uq_nation_join_request_pending",
            "nation_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_nation_join_requests_expires", "status", "expires_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NationWar(Base):
    __tablename__ = "nation_wars"
    __table_args__ = (
        Index("ix_nation_wars_nation_status", "nation_id", "status"),
        Index("ix_nation_wars_opponent_status", "opponent_nation_id", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        nullable=False,
    )
    opponent_nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    declared_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title_fa: Mapped[str] = mapped_column(String(100), nullable=False)
    description_fa: Mapped[str] = mapped_column(String(255), nullable=False)
    mission_type: Mapped[str] = mapped_column(String(10), nullable=False)
    target_count: Mapped[int] = mapped_column(nullable=False)
    reward_xr: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    reward_currency: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class UserMissionProgress(Base):
    __tablename__ = "user_mission_progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    progress: Mapped[int] = mapped_column(default=0, nullable=False)
    completed: Mapped[bool] = mapped_column(default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    claimed: Mapped[bool] = mapped_column(default=False, nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "mission_id", name="uq_user_mission"),
    )

class NationTreasury(Base):
    __tablename__ = "nation_treasury"

    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    balance_xr: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    balance_local: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    last_deposit_at: Mapped[datetime | None] = mapped_column(nullable=True)
    total_deposited: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )


class TreasuryLog(Base):
    __tablename__ = "treasury_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_xr: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow(), nullable=False
    )



class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(nullable=False)
    order: Mapped[int] = mapped_column(nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    title_fa: Mapped[str] = mapped_column(String(100), nullable=False)
    content_fa: Mapped[str] = mapped_column(String(4000), nullable=False)
    quiz_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    xp_reward: Mapped[int] = mapped_column(nullable=False, default=10)
    xr_reward: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0")
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("module_id", "order", name="uq_lesson_module_order"),
    )


class UserLessonProgress(Base):
    __tablename__ = "user_lesson_progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    lesson_id: Mapped[int] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="locked"
    )
    quiz_score: Mapped[int | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ai_questions_count: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "lesson_id", name="uq_user_lesson"),
    )


class UserXP(Base):
    __tablename__ = "user_xp"
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    total_xp: Mapped[int] = mapped_column(nullable=False, default=0)
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, default="beginner"
    )
    last_updated: Mapped[datetime] = mapped_column(
        nullable=False, default=lambda: datetime.utcnow()
    )