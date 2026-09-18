from __future__ import annotations

import logging
from aiogram import Dispatcher
from sqlalchemy import text

from app.database.session import async_session

logger = logging.getLogger("opexmoney.selftest")

EXPECTED_CALLBACKS = {
    "start_game", "show_help", "cancel_start", "join_", "found_nation",
    "first_trade_tutorial", "confirm_first_trade", "skip_first_trade",
    "market_main", "market_refresh", "market_buy", "market_sell",
    "market_chart", "market_history", "buy_", "buyq_", "cbuy_",
    "sell_", "sellq_", "csell_", "cancel_founder", "confirm_founder",
}

async def run_startup_smoke_test(dp: Dispatcher) -> bool:
    logger.info("SELFTEST|BEGIN|user-flow=START->ONBOARDING->DASHBOARD->MARKET->FOUNDER")
    ok = True

    try:
        router_names = {getattr(r, "name", "") for r in dp.sub_routers}
        logger.info("SELFTEST|PASS|routers=%s", ",".join(sorted(router_names)))
    except Exception:
        logger.exception("SELFTEST|FAIL|router-inspection")
        ok = False

    try:
        async with async_session() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
        logger.info("SELFTEST|PASS|database=SELECT_1")
    except Exception:
        logger.exception("SELFTEST|FAIL|database")
        ok = False

    # This is a route/flow contract test. It does not create users, nations, trades,
    # or Telegram messages in production data.
    route_text = " ".join(
        str(getattr(handler, "callback", "")) for router in dp.sub_routers
        for handler in getattr(router, "callback_query", {}).handlers
    )
    missing = []
    for expected in EXPECTED_CALLBACKS:
        if expected not in route_text:
            missing.append(expected)
    if missing:
        logger.error("SELFTEST|FAIL|missing-callback-contracts=%s", ",".join(missing))
        ok = False
    else:
        logger.info("SELFTEST|PASS|callback-contracts=%d", len(EXPECTED_CALLBACKS))

    logger.info("SELFTEST|%s|startup smoke test complete", "PASS" if ok else "FAIL")
    return ok
