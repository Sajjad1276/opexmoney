from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select

from app.database.models import CurrencyHolding, Nation, User
from app.database.session import async_session
from app.services.nation_service import convert_holding_to_xr

logger = logging.getLogger("opexmoney.repair_holding_liquidation")


async def repair_holding_liquidation() -> int:
    """
    One-time repair for positive holdings belonging to inactive nations.

    Holdings are preserved as rows and zeroed through the canonical
    liquidation function so XR credit + audit records are created consistently.
    """
    repaired = 0

    async with async_session() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(CurrencyHolding, User, Nation)
                    .join(User, User.user_id == CurrencyHolding.user_id)
                    .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
                    .where(
                        CurrencyHolding.amount > 0,
                        Nation.is_active.is_(False),
                    )
                    .order_by(Nation.nation_id.asc(), User.user_id.asc())
                    .with_for_update()
                )
            ).all()

            for holding, user, nation in rows:
                # ECONOMIC RULE: holdings liquidate to XR on kick/dissolve wherever you touch this logic
                result = await convert_holding_to_xr(user, nation, session)
                if result is None:
                    continue

                repaired += 1
                logger.warning(
                    "REPAIR|holding_liquidation|user_id=%s|nation_id=%s|amount=%s|rate=%s|xr_received=%s",
                    user.user_id,
                    nation.nation_id,
                    result["amount"],
                    result["rate"],
                    result["xr_received"],
                )

    logger.info("REPAIR|holding_liquidation|repaired=%s", repaired)
    return repaired


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    repaired = await repair_holding_liquidation()
    print(f"REPAIR|holding_liquidation|repaired={repaired}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
