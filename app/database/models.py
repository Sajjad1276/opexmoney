from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Nation(Base):
    __tablename__ = "nations"

    nation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    currency_code: Mapped[str] = mapped_column(String(4))
    founder_user_id: Mapped[int | None] = mapped_column(nullable=True)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=1.00)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(15), unique=True)
    home_nation_id: Mapped[int | None] = mapped_column(ForeignKey("nations.nation_id"))
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=500)
    role: Mapped[str] = mapped_column(String(20), default="player")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
