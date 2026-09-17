from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ActivityType(StrEnum):
    TRADE = "trade"
    LOGIN = "login"
    MISSION = "mission"


class Nation(Base):
    __tablename__ = "nations"
    nation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    currency_code: Mapped[str] = mapped_column(String(4))
    founder_user_id: Mapped[int | None] = mapped_column(nullable=True)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    rate_prev: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1.0000"), nullable=False)
    rate_24h_open: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1.0000"), nullable=False)
    trade_volume_24h: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    active_members_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nation_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_rate_update: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    home_nation_id: Mapped[int | None] = mapped_column(ForeignKey("nations.nation_id"))
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("500.00"), nullable=False)
    xr_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="player", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CurrencyHolding(Base):
    __tablename__ = "currency_holdings"
    __table_args__ = (UniqueConstraint("user_id", "nation_id", name="uq_currency_holding_user_nation"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_user_created", "user_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id"), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    spend_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    fee_xr: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserActivity(Base):
    __tablename__ = "user_activities"
    __table_args__ = (Index("ix_user_activities_nation_created", "nation_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id"), nullable=False)
    activity_type: Mapped[ActivityType] = mapped_column(SAEnum(ActivityType, name="activity_type", values_callable=lambda values: [item.value for item in values]), nullable=False)
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
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nation_id: Mapped[int] = mapped_column(ForeignKey("nations.nation_id", ondelete="CASCADE"), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    active_members: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TradePreview(Base):
    __tablename__ = "trade_previews"
    __table_args__ = (Index("ix_trade_previews_user_created", "user_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
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
