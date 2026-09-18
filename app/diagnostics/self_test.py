from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Dispatcher
from sqlalchemy import text

from app.database.session import async_session

logger = logging.getLogger("opexmoney.selftest")

CONTRACTS = {
    "start": ["CommandStart", "start_game", "show_help", "join_", "first_trade_tutorial", "confirm_first_trade", "skip_first_trade", "cancel_start"],
    "market": ["market_main", "market_refresh", "market_buy", "market_sell", "market_chart", "market_history", "buy_", "buyq_", "cbuy_", "sell_", "sellq_", "csell_"],
    "founder": ["found_nation", "cancel_founder", "confirm_founder"],
    "nation": ["🌍 ملت‌ها"],
    "sections": ["📊 پورتفولیو", "⚡ مأموریت", "🏆 رتبه‌بندی", "⚙️ تنظیمات"],
}

async def run_startup_smoke_test(dp: Dispatcher) -> bool:
    logger.info("SELFTEST|BEGIN|START->ONBOARDING->NATION->DASHBOARD->MARKET->FOUNDER")
    ok = True

    try:
        async with async_session() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
        logger.info("SELFTEST|PASS|database")
    except Exception:
        logger.exception("SELFTEST|FAIL|database")
        ok = False

    try:
        routers = {getattr(router, "name", ""): router for router in dp.sub_routers}
        logger.info("SELFTEST|PASS|routers=%s", ",".join(sorted(routers)))
        for router_name, tokens in CONTRACTS.items():
            router = routers.get(router_name)
            if router is None:
                logger.error("SELFTEST|FAIL|router=%s|missing", router_name)
                ok = False
                continue
            source = Path("/app") / ("app/handlers/" + router_name + ".py")
            if router_name == "start":
                source = Path("/app/app/handlers/start.py")
            elif router_name == "market":
                source = Path("/app/app/handlers/market.py")
            elif router_name == "founder":
                source = Path("/app/app/handlers/founder.py")
            elif router_name == "nation":
                source = Path("/app/app/handlers/nation.py")
            elif router_name == "sections":
                source = Path("/app/app/handlers/sections.py")
            body = source.read_text(encoding="utf-8")
            missing = [token for token in tokens if token not in body]
            if missing:
                logger.error("SELFTEST|FAIL|contract=%s|missing=%s", router_name, ",".join(missing))
                ok = False
            else:
                logger.info("SELFTEST|PASS|contract=%s|checks=%d", router_name, len(tokens))
    except Exception:
        logger.exception("SELFTEST|FAIL|route-contracts")
        ok = False

    logger.info("SELFTEST|%s|startup smoke test complete", "PASS" if ok else "FAIL")
    return ok
