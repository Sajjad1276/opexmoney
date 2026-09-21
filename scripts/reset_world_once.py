from __future__ import annotations

import asyncio
import os

import asyncpg
from redis.asyncio import Redis


PRESERVE = {"users", "missions", "lessons", "shop_items", "alembic_version"}
EXPECTED_WORLD_TABLES = {
    "bot_groups",
    "currency_holdings",
    "transactions",
    "price_alerts",
    "user_activities",
    "nation_member_history",
    "rate_history",
    "trade_previews",
    "nation_ranks",
    "proposals",
    "votes",
    "rule_overrides",
    "governance_ledger",
    "player_temporal_profiles",
    "behavior_snapshots",
    "nation_members",
    "nation_logs",
    "nation_join_requests",
    "nation_invite_links",
    "nation_wars",
    "nation_treasury",
    "treasury_logs",
    "nation_telegram_members",
    "nation_founding_drafts",
    "currency_market_states",
    "price_movement_receipts",
    "world_events",
    "ai_usage_logs",
    "decision_snapshots",
    "user_purchases",
    "user_mission_progress",
    "user_lesson_progress",
    "user_xp",
    "nations",
}


async def reset_database() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE users
                SET home_nation_id = NULL,
                    balance = 0,
                    xr_balance = 0,
                    role = 'player',
                    is_ai = FALSE,
                    ai_strategy = 'balanced',
                    deleted_at = NULL
                """
            )

            existing = {
                row["table_name"]
                for row in await conn.fetch(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    """
                )
            }
            targets = (EXPECTED_WORLD_TABLES & existing) - PRESERVE

            # Delete in child-before-parent order so preserved users remain intact.
            rows = await conn.fetch(
                """
                SELECT tc.table_name AS child_table,
                       ccu.table_name AS parent_table
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                  AND ccu.table_schema = 'public'
                """
            )
            children_of = {table: set() for table in targets}
            for child, parent in rows:
                if child in targets and parent in targets and child != parent:
                    children_of[parent].add(child)

            remaining = set(targets)
            delete_order: list[str] = []
            while remaining:
                leaves = sorted(
                    table
                    for table in remaining
                    if not (children_of.get(table, set()) & remaining)
                )
                if not leaves:
                    raise RuntimeError(
                        "reset delete order has an FK cycle: "
                        + ", ".join(sorted(remaining))
                    )
                delete_order.extend(leaves)
                remaining.difference_update(leaves)

            for table in delete_order:
                await conn.execute(f'DELETE FROM public."{table}"')

            await conn.execute(
                """
                UPDATE users
                SET home_nation_id = NULL,
                    balance = 0,
                    xr_balance = 0,
                    role = 'player',
                    is_ai = FALSE,
                    ai_strategy = 'balanced',
                    deleted_at = NULL
                """
            )

            nations = await conn.fetchval("SELECT COUNT(*) FROM nations")
            holdings = await conn.fetchval("SELECT COUNT(*) FROM currency_holdings")
            memberships = await conn.fetchval("SELECT COUNT(*) FROM nation_members")
            linked_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE home_nation_id IS NOT NULL"
            )
            ai_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE is_ai = TRUE"
            )

            if any((nations, holdings, memberships, linked_users, ai_users)):
                raise RuntimeError(
                    "reset verification failed: "
                    f"nations={nations} holdings={holdings} memberships={memberships} "
                    f"linked_users={linked_users} ai_users={ai_users}"
                )
    finally:
        await conn.close()


async def reset_pressure() -> int:
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        keys = [key async for key in redis.scan_iter(match="pressure:*", count=500)]
        if keys:
            await redis.delete(*keys)
        return len(keys)
    finally:
        await redis.aclose()


async def main() -> None:
    await reset_database()
    deleted_pressure = await reset_pressure()
    print(
        "WORLD_RESET|completed=true|nations=0|holdings=0|memberships=0|"
        "linked_users=0|ai_users=0|pressure_keys="
        + str(deleted_pressure),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
