from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import inspect, text

from app.database.models import Base
from app.database.session import async_session, engine
from app.services.rules.registry import RULE_REGISTRY, validate_registry
from app.services.rules.resolver import resolve

logger = logging.getLogger("opexmoney.selftest")

CONTRACTS = {
    "start": [
        "CommandStart",
        "start_game",
        "show_help",
        "join_",
        "first_trade_tutorial",
        "confirm_first_trade",
        "skip_first_trade",
        "cancel_start",
    ],
    "market": [
        "market_main",
        "market_refresh",
        "market_buy",
        "market_sell",
        "market_chart",
        "market_history",
        "buy_",
        "buyq_",
        "cbuy_",
        "sell_",
        "sellq_",
        "csell_",
    ],
    "founder": [
        "found_nation",
        "cancel_founder",
        "confirm_founder",
    ],
    "nation": ["🌍 ملت‌ها"],
    "sections": [
        "📊 پورتفولیو",
        "⚡ مأموریت",
        "🏆 رتبه‌بندی",
        "⚙️ تنظیمات",
    ],
    "governance": [
        "governance_main",
        "gov_active",
        "gov_new",
        "gov_voting",
        "gov_history",
        "gov_vote",
        "gov_revoke",
    ],
}

EXPECTED_JOB_IDS = {
    "rate_engine_15m",
    "nation_rank_hourly",
    "governance_cycle",
    "daily_market_reset",
    "nation_join_request_expiration",
    "nation_weekly_ai_report",
}


async def _table_exists(table_name: str) -> bool:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).has_table(table_name)
        )


async def run_startup_smoke_test(
    dp: Dispatcher,
    scheduler: AsyncIOScheduler | None = None,
) -> bool:
    logger.info(
        "SELFTEST|BEGIN|START->ONBOARDING->NATION->DASHBOARD->MARKET->FOUNDER->GOVERNANCE"
    )
    ok = True

    registry_errors = validate_registry()
    if registry_errors:
        logger.error(
            "SELFTEST|FAIL|registry|%s",
            " | ".join(registry_errors),
        )
        ok = False
    else:
        logger.info(
            "SELFTEST|PASS|registry|keys=%d",
            len(RULE_REGISTRY),
        )

    try:
        async with async_session() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
                for key in RULE_REGISTRY:
                    await resolve(session, key)

        logger.info("SELFTEST|PASS|database+resolver")
    except Exception:
        logger.exception("SELFTEST|FAIL|database+resolver")
        ok = False

    try:
        phase2_tables = (
            "proposals",
            "votes",
            "rule_overrides",
            "governance_ledger",
            "player_temporal_profiles",
            "behavior_snapshots",
        )
        nation_management_tables = (
            "nation_members",
            "nation_logs",
            "nation_join_requests",
            "nation_wars",
        )
        missing_phase2 = [
            table_name
            for table_name in phase2_tables
            if not await _table_exists(table_name)
        ]
        missing_nation_management = [
            table_name
            for table_name in nation_management_tables
            if not await _table_exists(table_name)
        ]
        if missing_phase2:
            logger.error(
                "SELFTEST|FAIL|phase2-tables|missing=%s",
                ",".join(missing_phase2),
            )
            ok = False
        else:
            logger.info("SELFTEST|PASS|phase2-tables")
        if missing_nation_management:
            logger.error(
                "SELFTEST|FAIL|nation-management-tables|missing=%s",
                ",".join(missing_nation_management),
            )
            ok = False
        else:
            logger.info("SELFTEST|PASS|nation-management-tables")
    except Exception:
        logger.exception("SELFTEST|FAIL|schema-tables")
        ok = False

    try:
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("nations")
                }
            )
        required_nation_columns = {
            "join_policy",
            "personality",
            "invite_code",
            "treasury",
        }
        missing_nation_columns = sorted(required_nation_columns - columns)
        if missing_nation_columns:
            logger.error(
                "SELFTEST|FAIL|nation-management-columns|missing=%s",
                ",".join(missing_nation_columns),
            )
            ok = False
        else:
            logger.info("SELFTEST|PASS|nation-management-columns")
    except Exception:
        logger.exception("SELFTEST|FAIL|nation-management-columns")
        ok = False

    try:
        root = Path(__file__).resolve().parents[2]
        routers = {getattr(router, "name", ""): router for router in dp.sub_routers}
        for router_name, tokens in CONTRACTS.items():
            router = routers.get(router_name)
            if router is None:
                logger.error(
                    "SELFTEST|FAIL|router=%s|missing",
                    router_name,
                )
                ok = False
                continue

            source = root / "app" / "handlers" / f"{router_name}.py"
            body = source.read_text(encoding="utf-8")
            missing = [token for token in tokens if token not in body]
            if missing:
                logger.error(
                    "SELFTEST|FAIL|contract=%s|missing=%s",
                    router_name,
                    ",".join(missing),
                )
                ok = False
            else:
                logger.info(
                    "SELFTEST|PASS|contract=%s|checks=%d",
                    router_name,
                    len(tokens),
                )
    except Exception:
        logger.exception("SELFTEST|FAIL|route-contracts")
        ok = False

    if scheduler is not None:
        missing_jobs = [
            job_id
            for job_id in EXPECTED_JOB_IDS
            if scheduler.get_job(job_id) is None
        ]
        if missing_jobs:
            logger.error(
                "SELFTEST|FAIL|scheduler|missing=%s",
                ",".join(missing_jobs),
            )
            ok = False
        else:
            logger.info(
                "SELFTEST|PASS|scheduler|jobs=%d",
                len(EXPECTED_JOB_IDS),
            )

    expected_model_tables = {
        "proposals",
        "votes",
        "rule_overrides",
        "governance_ledger",
        "player_temporal_profiles",
        "behavior_snapshots",
    }
    missing_metadata = expected_model_tables.difference(Base.metadata.tables)
    if missing_metadata:
        logger.error(
            "SELFTEST|FAIL|metadata|missing=%s",
            ",".join(sorted(missing_metadata)),
        )
        ok = False

    logger.info(
        "SELFTEST|%s|startup smoke test complete",
        "PASS" if ok else "FAIL",
    )
    return ok
