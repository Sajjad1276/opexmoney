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
        "CommandStart", "start_game", "show_help", "join_",
        "first_trade_tutorial", "confirm_first_trade", "skip_first_trade",
        "cancel_start",
    ],
    "market": ["market_main", "market_refresh", "market_buy", "market_sell",
               "market_chart", "market_history", "buy_", "buyq_", "cbuy_",
               "sell_", "sellq_", "csell_"],
    "founder": ["found_nation", "cancel_founder", "confirm_founder"],
    "nation": ["🌍 ملت‌ها"],
    "sections": ["📊 پورتفولیو", "⚡ مأموریت", "🏆 رتبه‌بندی", "⚙️ تنظیمات"],
    "governance": ["governance_main", "gov_active", "gov_new", "gov_voting",
                   "gov_history", "gov_vote", "gov_revoke"],
}

EXPECTED_JOB_IDS = {
    "rate_engine_15m", "nation_rank_hourly", "governance_cycle",
    "daily_market_reset", "nation_join_request_expiration",
    "nation_weekly_ai_report", "price_alert_checker_5m",
}

EXPECTED_ROUTER_NAMES = {
    "onboarding_fix", "start", "market", "founder", "nation_management",
    "nation", "governance", "sections",
}
EXPECTED_AI_ROUTER_NAMES = {"ai"}


async def _table_exists(table_name: str) -> bool:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).has_table(table_name)
        )


def _all_handler_source(root: Path) -> str:
    parts = []
    for source in (root / "app" / "handlers").glob("*.py"):
        parts.append(source.read_text(encoding="utf-8"))
    ai_init = root / "ai" / "__init__.py"
    if ai_init.exists():
        parts.append(ai_init.read_text(encoding="utf-8"))
    return "\n".join(parts)


async def run_startup_smoke_test(
    dp: Dispatcher,
    scheduler: AsyncIOScheduler | None = None,
) -> bool:
    logger.info("SELFTEST|BEGIN|START->ONBOARDING->NATION->DASHBOARD->MARKET->FOUNDER->GOVERNANCE")
    ok = True

    registry_errors = validate_registry()
    if registry_errors:
        logger.error("SELFTEST|FAIL|registry|%s", " | ".join(registry_errors))
        ok = False
    else:
        logger.info("SELFTEST|PASS|registry|keys=%d", len(RULE_REGISTRY))

    try:
        from app.diagnostics.flow_health import run_flow_health_test
        flow_report = run_flow_health_test()
        if flow_report.errors:
            logger.error("SELFTEST|FAIL|flow-health|%s", " | ".join(flow_report.errors))
            ok = False
        else:
            logger.info(
                "SELFTEST|PASS|flow-health|%s|warnings=%d",
                " ".join(f"{key}={value}" for key, value in flow_report.metrics.items()),
                len(flow_report.warnings),
            )
            for warning in flow_report.warnings:
                logger.warning("SELFTEST|WARN|flow-health|%s", warning)
    except Exception:
        logger.exception("SELFTEST|FAIL|flow-health")
        ok = False

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
        async with async_session() as session:
            active_nations = await session.scalar(
                text("SELECT COUNT(*) FROM nations WHERE is_active = TRUE")
            )
        if not active_nations:
            logger.error("SELFTEST|FAIL|active-nations|count=0")
            ok = False
        else:
            logger.info("SELFTEST|PASS|active-nations|count=%d", active_nations)
    except Exception:
        logger.exception("SELFTEST|FAIL|active-nations")
        ok = False

    try:
        phase2_tables = ("proposals", "votes", "rule_overrides", "governance_ledger",
                         "player_temporal_profiles", "behavior_snapshots")
        nation_tables = (
            "nation_members",
            "nation_logs",
            "nation_join_requests",
            "nation_wars",
            "nation_telegram_members",
        )
        missing_phase2 = [x for x in phase2_tables if not await _table_exists(x)]
        missing_nation = [x for x in nation_tables if not await _table_exists(x)]
        if missing_phase2:
            logger.error("SELFTEST|FAIL|phase2-tables|missing=%s", ",".join(missing_phase2)); ok = False
        else:
            logger.info("SELFTEST|PASS|phase2-tables")
        if missing_nation:
            logger.error("SELFTEST|FAIL|nation-management-tables|missing=%s", ",".join(missing_nation)); ok = False
        else:
            logger.info("SELFTEST|PASS|nation-management-tables")
    except Exception:
        logger.exception("SELFTEST|FAIL|schema-tables"); ok = False

    try:
        async with engine.connect() as connection:
            identity_rows = await connection.execute(text("""
                SELECT
                    c.relname,
                    a.attidentity,
                    pg_get_expr(d.adbin, d.adrelid) AS default_expr
                FROM pg_class c
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'id'
                LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relname IN ('user_activities', 'rate_history')
            """))
            identity = {row[0]: (row[1], row[2]) for row in identity_rows}
        identity_ok = all(
            name in identity and (
                identity[name][0] in ("a", "d")
                or (identity[name][1] or "").lower().startswith("nextval(")
            )
            for name in ("user_activities", "rate_history")
        )
        if not identity_ok:
            logger.error("SELFTEST|FAIL|bigint-identities|user_activities/rate_history id is not generated|details=%s", identity)
            ok = False
        else:
            logger.info("SELFTEST|PASS|bigint-identities")
    except Exception:
        logger.exception("SELFTEST|FAIL|bigint-identities"); ok = False

    try:
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {c["name"] for c in inspect(sync_connection).get_columns("nations")}
            )
        required = {"join_policy", "personality", "invite_code", "treasury"}
        missing = sorted(required - columns)
        if missing:
            logger.error("SELFTEST|FAIL|nation-management-columns|missing=%s", ",".join(missing)); ok = False
        else:
            logger.info("SELFTEST|PASS|nation-management-columns")
    except Exception:
        logger.exception("SELFTEST|FAIL|nation-management-columns"); ok = False

    try:
        root = Path(__file__).resolve().parents[2]
        from app.handlers.start import username_exists as _username_exists
        if not callable(_username_exists):
            logger.error("SELFTEST|FAIL|onboarding-imports|username_exists is not callable")
            ok = False
        else:
            logger.info("SELFTEST|PASS|onboarding-imports|username_exists")
        source_all = _all_handler_source(root)
        routers = {getattr(router, "name", ""): router for router in dp.sub_routers}
        for router_name, tokens in CONTRACTS.items():
            router = routers.get(router_name)
            if router is None:
                logger.error("SELFTEST|FAIL|router=%s|missing", router_name); ok = False
                continue
            missing = [token for token in tokens if token not in source_all]
            if missing:
                logger.error("SELFTEST|FAIL|contract=%s|missing=%s", router_name, ",".join(missing)); ok = False
            else:
                logger.info("SELFTEST|PASS|contract=%s|checks=%d", router_name, len(tokens))

        registered_names = set(routers)
        missing_routers = sorted(EXPECTED_ROUTER_NAMES - registered_names)
        if missing_routers:
            logger.error("SELFTEST|FAIL|routers|missing=%s", ",".join(missing_routers)); ok = False
        else:
            logger.info("SELFTEST|PASS|routers|count=%d", len(EXPECTED_ROUTER_NAMES))

        missing_ai = sorted(EXPECTED_AI_ROUTER_NAMES - registered_names)
        if missing_ai:
            logger.error("SELFTEST|FAIL|ai-router|missing=%s", ",".join(missing_ai)); ok = False
        else:
            logger.info("SELFTEST|PASS|ai-router|name=ai_companion")
    except Exception:
        logger.exception("SELFTEST|FAIL|route-contracts"); ok = False

    if scheduler is not None:
        missing_jobs = [job_id for job_id in EXPECTED_JOB_IDS if scheduler.get_job(job_id) is None]
        if missing_jobs:
            logger.error("SELFTEST|FAIL|scheduler|missing=%s", ",".join(missing_jobs)); ok = False
        else:
            logger.info("SELFTEST|PASS|scheduler|jobs=%d", len(EXPECTED_JOB_IDS))

    missing_metadata = {
        "proposals", "votes", "rule_overrides", "governance_ledger",
        "player_temporal_profiles", "behavior_snapshots"
    }.difference(Base.metadata.tables)
    if missing_metadata:
        logger.error("SELFTEST|FAIL|metadata|missing=%s", ",".join(sorted(missing_metadata))); ok = False

    logger.info("SELFTEST|%s|startup smoke test complete", "PASS" if ok else "FAIL")
    return ok
