from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Transaction


class TransactionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        nation_id: int,
        tx_type: str,
        amount: Decimal,
        rate: Decimal,
    ) -> Transaction:
        transaction = Transaction(
            user_id=user_id,
            nation_id=nation_id,
            transaction_type=tx_type,
            spend_xr=Decimal(str(amount)),
            amount=Decimal(str(amount)),
            fee_xr=Decimal("0"),
            rate=Decimal(str(rate)),
        )
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    async def get_user_history(
        self,
        user_id: int,
        limit: int = 10,
    ) -> list[Transaction]:
        safe_limit = max(1, min(int(limit), 100))
        result = await self.session.execute(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(safe_limit)
        )
        return list(result.scalars().all())

    async def get_volume_since(
        self,
        nation_id: int,
        since: datetime,
    ) -> Decimal:
        total = await self.session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.nation_id == nation_id,
                Transaction.created_at >= since,
            )
        )
        return Decimal(str(total or 0))
