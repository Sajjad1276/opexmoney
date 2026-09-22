from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    Enum as SAEnum,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym
from sqlalchemy.types import TypeDecorator
from .session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCComparableDateTime(datetime):
    """UTC-aware datetime that safely compares with legacy naive UTC datetimes."""

    @staticmethod
    def _coerce(other: datetime | object) -> datetime | object:
        if isinstance(other, datetime) and other.tzinfo is None:
            return other.replace(tzinfo=timezone.utc)
        return other

    def __lt__(self, other):
        return super().__lt__(self._coerce(other))

    def __le__(self, other):
        return super().__le__(self._coerce(other))

    def __gt__(self, other):
        return super().__gt__(self._coerce(other))

    def __ge__(self, other):
        return super().__ge__(self._coerce(other))

    def __eq__(self, other):
        try:
            return super().__eq__(self._coerce(other))
        except TypeError:
            return False


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC as TIMESTAMP WITH TIME ZONE and normalize legacy UTC inputs."""

    impl = DateTime
    cache_ok = True
    timezone = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return UTCComparableDateTime.fromtimestamp(
            value.timestamp(),
            tz=timezone.utc,
        )


def enum_type(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda values: [item.value for item in values],
    )


class ActivityType(StrEnum):
    TRADE = "trade"
    LOGIN = "login"
    MISSION = "mission"


class JoinRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class NationMemberRole(StrEnum):
    FOUNDER = "founder"
    MINISTER = "minister"
    TRADER = "trader"
    CITIZEN = "citizen"


class WarStatus(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    DRAW = "draw"


class AITier(StrEnum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    DIAMOND = "diamond"
    ELITE = "elite"


class MarketTriggerSource(StrEnum):
    USER_TRADE = "user_trade"
    AI_TRADE = "ai_trade"
    SCHEDULER = "scheduler"
    EVENT = "event"
    GOVERNANCE = "governance"


class WorldEventType(StrEnum):
    ECONOMIC_CRISIS = "economic_crisis"
    TRADE_DEAL = "trade_deal"
    INVESTOR_ENTRY = "investor_entry"
    INVESTOR_EXIT = "investor_exit"
    RESOURCE_DISCOVERY = "resource_discovery"
    MARKET_BOOM = "market_boom"
    MARKET_CRASH = "market_crash"
    POLITICAL_CHANGE = "political_change"
    NATION_FOUNDED = "nation_founded"
    NATION_DISSOLVED = "nation_dissolved"
    WAR_DECLARED = "war_declared"
    PEACE_SIGNED = "peace_signed"


class WorldEventScope(StrEnum):
    LOCAL = "local"
    NATIONAL = "national"
    GLOBAL = "global"


class WorldEventEffectType(StrEnum):
    RATE_BOOST = "rate_boost"
    RATE_DROP = "rate_drop"
    LIQUIDITY_CHANGE = "liquidity_change"
    CONFIDENCE_CHANGE = "confidence_change"
    TREASURY_IMPACT = "treasury_impact"


class WorldEventSource(StrEnum):
    SCHEDULER = "scheduler"
    AI_WORLD = "ai_world"
    USER_ACTION = "user_action"
    GOVERNANCE = "governance"


class DecisionAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class ShopCategory(StrEnum):
    BOOST = "boost"
    PERMANENT = "permanent"
    SUBSCRIPTION = "subscription"


class PaymentMethod(StrEnum):
    OPX = "opx"
    STARS = "stars"
    PROMO = "promo"


class AlertDirection(StrEnum):
    ABOVE = "above"
    BELOW = "below"


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
        Index("ix_nations_active_deleted", "is_active", "deleted_at"),
    )

    nation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    flag_emoji: Mapped[str | None] = mapped_column(String(10), nullable=True, default="🏴")
    currency_code: Mapped[str] = mapped_column(String(4), nullable=False)
    founder_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        default=Decimal("1.0000"),
        nullable=False,
    )
    rate_prev: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        default=Decimal("1.0000"),
        nullable=False,
    )
    rate_24h_open: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        default=Decimal("1.0000"),
        nullable=False,
    )
    trade_volume_24h: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        default=Decimal("0"),
        nullable=False,
    )
    active_members_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nation_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_rate_update: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    join_policy: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    personality: Mapped[str] = mapped_column(String(20), default="neutral", nullable=False)
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    invite_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    treasury: Mapped[Decimal] = mapped_column(
        Numeric(20, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class BotGroup(Base):
    __tablename__ = "bot_groups"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


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
    is_member: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    left_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


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
        Index("ix_nation_founding_drafts_expiry", "status", "expires_at"),
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
    status: Mapped[str] = mapped_column(String(20), default="WAITING_GROUP", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )

    founder: Mapped["User"] = relationship(
        "User",
        foreign_keys=[founder_user_id],
        lazy="selectin",
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        Index("ix_users_telegram_id", "user_id"),
        Index("ix_users_active_deleted", "deleted_at"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id = synonym("user_id")
    username: Mapped[str] = mapped_column(String(20), nullable=False)
    home_nation_id: Mapped[int | None] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="SET NULL"),
        nullable=True,
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("500.00"), nullable=False
    )
    xr_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default="player", nullable=False)
    ban_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_strategy: Mapped[str] = mapped_column(String(20), default="balanced", nullable=False)
    ai_tier: Mapped[AITier] = mapped_column(
        enum_type(AITier, "ai_tier"),
        default=AITier.BRONZE,
        server_default=AITier.BRONZE.value,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    home_nation: Mapped["Nation | None"] = relationship(
        "Nation", foreign_keys=[home_nation_id], lazy="selectin"
    )


class CurrencyHolding(Base):
    __tablename__ = "currency_holdings"
    __table_args__ = (
        UniqueConstraint("user_id", "nation_id", name="uq_currency_holding_user_nation"),
        Index("ix_currency_holdings_user_nation", "user_id", "nation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_created", "user_id", "created_at"),
        Index("ix_transactions_nation_created_type", "nation_id", "created_at", "transaction_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id"), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(16), nullable=False)
    spend_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    fee_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class PriceAlert(Base):
    __tablename__ = "price_alerts"
    __table_args__ = (
        Index("ix_price_alerts_user_created", "user_id", "created_at"),
        Index("ix_price_alerts_pending", "is_triggered", "currency_code"),
        Index("ix_price_alerts_user_triggered", "user_id", "is_triggered"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(String(4), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    direction: Mapped[AlertDirection] = mapped_column(
        enum_type(AlertDirection, "price_alert_direction"),
        default=AlertDirection.ABOVE,
        nullable=False,
    )
    is_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triggered = synonym("is_triggered")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    triggered_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    triggered_value: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")


class UserActivity(Base):
    __tablename__ = "user_activities"
    __table_args__ = (
        Index("ix_user_activities_nation_created", "nation_id", "created_at"),
        Index("ix_user_activities_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id"), nullable=False)
    activity_type: Mapped[ActivityType] = mapped_column(
        PGEnum(
            ActivityType,
            name="activity_type",
            create_type=False,
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class NationMemberHistory(Base):
    __tablename__ = "nation_member_history"
    __table_args__ = (
        Index("ix_nation_member_history_nation_recorded", "nation_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class RateHistory(Base):
    __tablename__ = "rate_history"
    __table_args__ = (
        Index("ix_rate_history_nation_calculated", "nation_id", "calculated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    active_members: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    dominant_cause: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    pressure_signal: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
        default=Decimal("0"),
        nullable=True,
    )
    foreign_signal: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
        default=Decimal("0"),
        nullable=True,
    )
    activity_score: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
        default=Decimal("0"),
        nullable=True,
    )
    trade_score: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
        default=Decimal("0"),
        nullable=True,
    )
    growth_score: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
        default=Decimal("0"),
        nullable=True,
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class TradePreview(Base):
    __tablename__ = "trade_previews"
    __table_args__ = (
        Index("ix_trade_previews_user_created", "user_id", "created_at"),
        Index("ix_trade_previews_lookup", "user_id", "nation_id", "side", "spend", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    preview_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class NationRank(Base):
    __tablename__ = "nation_ranks"

    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        Index("ix_proposals_status_closing", "status", "voting_closes_at"),
        Index("ix_proposals_proposer_created", "proposer_player_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    proposer_player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    proposed_value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    voting_opens_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    voting_closes_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    effective_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    proposer: Mapped["User"] = relationship(
        "User", foreign_keys=[proposer_player_id], lazy="selectin"
    )


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("proposal_id", "player_id", name="uq_vote_proposal_player"),
        Index("ix_votes_proposal", "proposal_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    choice: Mapped[str] = mapped_column(String(10), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    proposal: Mapped["Proposal"] = relationship("Proposal", lazy="selectin")
    player: Mapped["User"] = relationship("User", lazy="selectin")


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
    source_proposal_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    active_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    active_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    suspended_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    source_proposal: Mapped["Proposal | None"] = relationship("Proposal", lazy="selectin")


class GovernanceLedger(Base):
    __tablename__ = "governance_ledger"
    __table_args__ = (Index("ix_governance_ledger_at", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    actor_player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    actor: Mapped["User | None"] = relationship(
        "User", foreign_keys=[actor_player_id], lazy="selectin"
    )


class PlayerTemporalProfile(Base):
    __tablename__ = "player_temporal_profiles"
    __table_args__ = (Index("ix_temporal_next_rotation", "next_rotation_at"),)

    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    peak_hour_start: Mapped[int] = mapped_column(Integer, nullable=False)
    peak_window_hours: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    peak_multiplier: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), default=Decimal("1.5"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    next_rotation_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    player: Mapped["User"] = relationship("User", lazy="selectin")


class BehaviorSnapshot(Base):
    __tablename__ = "behavior_snapshots"
    __table_args__ = (Index("ix_behavior_snapshots_at", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
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


class NationMembership(Base):
    __tablename__ = "nation_members"
    __table_args__ = (
        UniqueConstraint("nation_id", "user_id", name="uq_nation_member_nation_user"),
        Index("ix_nation_members_nation_role", "nation_id", "role"),
        Index("ix_nation_members_user_active", "user_id", "is_active"),
        Index(
            "uq_nation_members_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[NationMemberRole] = mapped_column(
        enum_type(NationMemberRole, "nation_member_role"),
        nullable=False,
        default=NationMemberRole.CITIZEN,
    )
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship("User", lazy="selectin")
    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


NationMember = NationMembership


class NationLog(Base):
    __tablename__ = "nation_logs"
    __table_args__ = (
        Index("ix_nation_logs_nation_created", "nation_id", "created_at"),
        Index("ix_nation_logs_nation_action", "nation_id", "action_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")
    actor: Mapped["User | None"] = relationship(
        "User", foreign_keys=[actor_id], lazy="selectin"
    )
    target: Mapped["User | None"] = relationship(
        "User", foreign_keys=[target_id], lazy="selectin"
    )


class NationJoinRequest(Base):
    __tablename__ = "nation_join_requests"
    __table_args__ = (
        Index(
            "uq_nation_join_request_pending",
            "nation_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_nation_join_requests_expires", "status", "expires_at"),
        Index("ix_nation_join_requests_requested_at", "requested_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JoinRequestStatus] = mapped_column(
        enum_type(JoinRequestStatus, "nation_join_request_status"),
        default=JoinRequestStatus.PENDING,
        server_default=JoinRequestStatus.PENDING.value,
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    created_at = synonym("requested_at")
    reviewed_at = synonym("resolved_at")
    reviewed_by = synonym("resolved_by")

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], lazy="selectin"
    )
    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")
    resolver: Mapped["User | None"] = relationship(
        "User", foreign_keys=[resolved_by], lazy="selectin"
    )


class NationInviteLink(Base):
    __tablename__ = "nation_invite_links"
    __table_args__ = (
        UniqueConstraint("token", name="uq_nation_invite_link_token"),
        CheckConstraint(
            "(is_used = FALSE) OR (used_by IS NOT NULL)",
            name="ck_nation_invite_link_used_consistency",
        ),
        Index("ix_nation_invite_links_nation_expiry", "nation_id", "expires_at"),
        Index("ix_nation_invite_links_unused_expiry", "is_used", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(
        String(128), default=lambda: str(uuid4()), nullable=False
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    used_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")
    creator: Mapped["User"] = relationship(
        "User", foreign_keys=[created_by], lazy="selectin"
    )
    consumer: Mapped["User | None"] = relationship(
        "User", foreign_keys=[used_by], lazy="selectin"
    )


class NationWar(Base):
    __tablename__ = "nation_wars"
    __table_args__ = (
        Index("ix_nation_wars_nation_status", "nation_id", "status"),
        Index("ix_nation_wars_opponent_status", "opponent_nation_id", "status"),
        Index("ix_nation_wars_status_ends_at", "status", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    opponent_nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[WarStatus] = mapped_column(
        enum_type(WarStatus, "nation_war_status"),
        default=WarStatus.ACTIVE,
        server_default=WarStatus.ACTIVE.value,
        nullable=False,
    )
    declared_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )

    nation: Mapped["Nation"] = relationship(
        "Nation", foreign_keys=[nation_id], lazy="selectin"
    )
    opponent_nation: Mapped["Nation"] = relationship(
        "Nation", foreign_keys=[opponent_nation_id], lazy="selectin"
    )


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title_fa: Mapped[str] = mapped_column(String(100), nullable=False)
    description_fa: Mapped[str] = mapped_column(String(255), nullable=False)
    mission_type: Mapped[str] = mapped_column(String(10), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), default="custom", server_default="custom", nullable=False)
    reward_xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    reward_xr: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    reward_currency: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserMissionProgress(Base):
    __tablename__ = "user_mission_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "mission_id", name="uq_user_mission"),
        Index("ix_user_mission_progress_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    mission: Mapped["Mission"] = relationship("Mission", lazy="selectin")


class NationTreasury(Base):
    __tablename__ = "nation_treasury"

    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), primary_key=True
    )
    balance_xr: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    balance_local: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    last_deposit_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    total_deposited: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")


class TreasuryLog(Base):
    __tablename__ = "treasury_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nation_id: Mapped[int] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    nation: Mapped["Nation"] = relationship("Nation", lazy="selectin")
    actor: Mapped["User | None"] = relationship(
        "User", foreign_keys=[actor_id], lazy="selectin"
    )


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint("module_id", "order", name="uq_lesson_module_order"),
        Index("ix_lessons_module_id", "module_id", "order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(Integer, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    title_fa: Mapped[str] = mapped_column(String(100), nullable=False)
    content_fa: Mapped[str] = mapped_column(String(4000), nullable=False)
    quiz_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    xp_reward: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    xr_reward: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserLessonProgress(Base):
    __tablename__ = "user_lesson_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "lesson_id", name="uq_user_lesson"),
        Index("ix_user_lesson_progress_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    lesson_id: Mapped[int] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), default="locked", nullable=False)
    quiz_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    ai_questions_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    lesson: Mapped["Lesson"] = relationship("Lesson", lazy="selectin")


class UserXP(Base):
    __tablename__ = "user_xp"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    total_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="beginner", nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")


class CurrencyMarketState(Base):
    __tablename__ = "currency_market_states"
    __table_args__ = (
        Index("ix_currency_market_state_currency_code", "currency_code"),
        CheckConstraint(
            "buy_pressure >= 0 AND buy_pressure <= 100",
            name="ck_market_buy_pressure",
        ),
        CheckConstraint(
            "sell_pressure >= 0 AND sell_pressure <= 100",
            name="ck_market_sell_pressure",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_market_confidence",
        ),
        CheckConstraint(
            "calculated_rate >= 0",
            name="ck_market_calculated_rate",
        ),
        CheckConstraint(
            "previous_rate >= 0",
            name="ck_market_previous_rate",
        ),
    )

    currency_code: Mapped[str] = mapped_column(String(4), primary_key=True)
    buy_pressure: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sell_pressure: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    volatility: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    foreign_demand: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    national_activity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    calculated_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("1.00000000"), nullable=False
    )
    previous_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("1.00000000"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class PriceMovementReceipt(Base):
    __tablename__ = "price_movement_receipts"
    __table_args__ = (
        Index(
            "ix_price_movement_receipts_currency_timestamp",
            "currency_code",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    currency_code: Mapped[str] = mapped_column(String(4), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    rate_before: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    rate_after: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    change_percent: Mapped[float] = mapped_column(Float, nullable=False)
    cause_primary: Mapped[str] = mapped_column(String(100), nullable=False)
    cause_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    triggered_by: Mapped[MarketTriggerSource] = mapped_column(
        enum_type(MarketTriggerSource, "market_trigger_source"), nullable=False
    )


class WorldEvent(Base):
    __tablename__ = "world_events"
    __table_args__ = (
        Index("ix_world_events_active_ends_at", "is_active", "ends_at"),
        Index("ix_world_events_nation_active", "affected_nation_id", "is_active"),
        CheckConstraint(
            "duration_minutes > 0",
            name="ck_world_event_duration_positive",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    event_type: Mapped[WorldEventType] = mapped_column(
        enum_type(WorldEventType, "world_event_type"), nullable=False
    )
    scope: Mapped[WorldEventScope] = mapped_column(
        enum_type(WorldEventScope, "world_event_scope"), nullable=False
    )
    affected_nation_id: Mapped[int | None] = mapped_column(
        ForeignKey("nations.nation_id", ondelete="SET NULL"), nullable=True
    )
    affected_currency: Mapped[str | None] = mapped_column(String(4), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    effect_type: Mapped[WorldEventEffectType] = mapped_column(
        enum_type(WorldEventEffectType, "world_event_effect_type"), nullable=False
    )
    effect_magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[WorldEventSource] = mapped_column(
        enum_type(WorldEventSource, "world_event_source"), nullable=False
    )
    announced_in_group: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    affected_nation: Mapped["Nation | None"] = relationship(
        "Nation", foreign_keys=[affected_nation_id], lazy="selectin"
    )


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_ai_usage_user_date"),
        Index("ix_ai_usage_logs_user_date", "user_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    advisor_questions_used: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    portfolio_scans_used: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    war_analysis_used: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")


class DecisionSnapshot(Base):
    __tablename__ = "decision_snapshots"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_decision_snapshot_transaction"),
        Index("ix_decision_snapshots_user_evaluated", "user_id", "is_evaluated"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[DecisionAction] = mapped_column(
        enum_type(DecisionAction, "decision_action"), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(String(4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    rate_at_decision: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    actual_outcome: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    alternative_outcome: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    opportunity_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    is_evaluated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", lazy="selectin")
    transaction: Mapped["Transaction"] = relationship("Transaction", lazy="selectin")


class ShopItem(Base):
    __tablename__ = "shop_items"
    __table_args__ = (
        UniqueConstraint("item_key", name="uq_shop_item_key"),
        Index("ix_shop_items_active_category", "is_active", "category"),
        Index("ix_shop_items_required_tier", "required_tier"),
    )

    item_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_opx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_tier: Mapped[AITier] = mapped_column(
        enum_type(AITier, "shop_required_tier"),
        default=AITier.BRONZE,
        server_default=AITier.BRONZE.value,
        nullable=False,
    )
    category: Mapped[ShopCategory] = mapped_column(
        enum_type(ShopCategory, "shop_category"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserPurchase(Base):
    __tablename__ = "user_purchases"
    __table_args__ = (
        Index("ix_user_purchases_user_active", "user_id", "is_active"),
        Index("ix_user_purchases_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(
        String(80), ForeignKey("shop_items.item_key", ondelete="RESTRICT"), nullable=False
    )
    purchased_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_type(PaymentMethod, "payment_method"), nullable=False
    )

    user: Mapped["User"] = relationship("User", lazy="selectin")
    item: Mapped["ShopItem"] = relationship("ShopItem", lazy="selectin")
