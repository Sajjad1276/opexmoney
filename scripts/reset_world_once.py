from __future__ import annotations

import asyncio
import os

import asyncpg


RESET_TABLES = {
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
    "nation_invite_links",
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


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
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

            row = await conn.fetchrow(
                """
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                  AND tc.table_name = 'users'
                  AND ccu.table_name = 'nations'
                LIMIT 1
                """
            )
            if row:
                await conn.execute(
                    f'ALTER TABLE users DROP CONSTRAINT "{row["constraint_name"]}"'
                )

            existing = {
                r["table_name"]
                for r in await conn.fetch(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    """
                )
            }
            targets = sorted(
                RESET_TABLES
                & existing
                - {"users", "missions", "lessons", "shop_items", "alembic_version"}
            )
            if targets:
                names = ", ".join(f'public."{name}"' for name in targets)
                await conn.execute(
                    f"TRUNCATE TABLE {names} RESTART IDENTITY"
                )

            await conn.execute(
                """
                ALTER TABLE users
                ADD CONSTRAINT fk_users_home_nation_world_reset
                FOREIGN KEY (home_nation_id)
                REFERENCES nations(nation_id)
                ON DELETE SET NULL
                """
            )

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

            remaining_nations = await conn.fetchval(
                "SELECT COUNT(*) FROM nations"
            )
            remaining_holdings = await conn.fetchval(
                "SELECT COUNT(*) FROM currency_holdings"
            )
            linked_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE home_nation_id IS NOT NULL"
            )

            if remaining_nations or remaining_holdings or linked_users:
                raise RuntimeError(
                    f"reset verification failed: nations={remaining_nations}, "
                    f"holdings={remaining_holdings}, linked_users={linked_users}"
                )

            print(
                "WORLD_RESET|completed=true|nations=0|holdings=0|linked_users=0",
                flush=True,
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
