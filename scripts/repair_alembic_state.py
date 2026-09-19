from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

REVISION_CHAIN = [
    "0001_initial_schema",
    "0002_founder_constraints",
    "0003_living_economy_protocol",
    "0004_onboarding_username_length",
    "0005_nation_management",
    "0006_bigint_history_identities",
    "0007_seed_starter_nations",
]

BASE_TABLES = {
    "nations",
    "users",
    "currency_holdings",
    "transactions",
    "user_activities",
    "nation_member_history",
    "rate_history",
    "trade_previews",
    "nation_ranks",
}

GOVERNANCE_TABLES = {
    "proposals",
    "votes",
    "rule_overrides",
    "governance_ledger",
    "player_temporal_profiles",
    "behavior_snapshots",
}

NATION_MANAGEMENT_TABLES = {
    "nation_members",
    "nation_logs",
    "nation_join_requests",
    "nation_wars",
}


async def table_exists(conn: asyncpg.Connection, table_name: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = $1
            )
            """,
            table_name,
        )
    )


async def all_tables_exist(conn: asyncpg.Connection, names: set[str]) -> bool:
    for name in names:
        if not await table_exists(conn, name):
            return False
    return True


async def index_exists(conn: asyncpg.Connection, index_name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL",
            f"public.{index_name}",
        )
    )


async def column_has_generated_id(
    conn: asyncpg.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = $1
                  AND column_name = $2
                  AND (
                      is_identity = 'YES'
                      OR column_default LIKE 'nextval(%%'
                  )
            )
            """,
            table_name,
            column_name,
        )
    )


async def username_width(conn: asyncpg.Connection) -> int | None:
    value = await conn.fetchval(
        """
        SELECT character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name = 'username'
        """
    )
    return int(value) if value is not None else None


async def detect_revision(conn: asyncpg.Connection) -> str | None:
    if not await all_tables_exist(conn, BASE_TABLES):
        return None

    # 0002 is only considered applied when its own schema markers are present.
    if not await table_exists(conn, "bot_groups"):
        return "0001_initial_schema"

    if not await index_exists(conn, "uq_nations_currency_code"):
        return "0001_initial_schema"

    if not await index_exists(conn, "uq_nations_active_group"):
        return "0001_initial_schema"

    highest = "0002_founder_constraints"

    if await all_tables_exist(conn, GOVERNANCE_TABLES):
        highest = "0003_living_economy_protocol"
    else:
        return highest

    width = await username_width(conn)
    if width is not None and width >= 20:
        highest = "0004_onboarding_username_length"
    else:
        return highest

    required_columns = {
        "join_policy",
        "personality",
        "invite_code",
        "treasury",
    }
    columns = {
        row["column_name"]
        for row in await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'nations'
              AND column_name = ANY($1::text[])
            """,
            list(required_columns),
        )
    }

    if required_columns.issubset(columns) and await all_tables_exist(
        conn, NATION_MANAGEMENT_TABLES
    ):
        highest = "0005_nation_management"
        if (
            await column_has_generated_id(conn, "user_activities", "id")
            and await column_has_generated_id(conn, "rate_history", "id")
        ):
            highest = "0006_bigint_history_identities"

    return highest


async def ensure_version_tracking(
    conn: asyncpg.Connection,
    detected_revision: str | None,
) -> None:
    exists = await table_exists(conn, "alembic_version")

    if not exists:
        if detected_revision is None:
            return
        await conn.execute(
            """
            CREATE TABLE alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            )
            """
        )
        await conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES ($1)",
            detected_revision,
        )
        print(
            f"REPAIR|created alembic_version|stamped={detected_revision}",
            flush=True,
        )
        return

    rows = await conn.fetch("SELECT version_num FROM alembic_version")
    if len(rows) > 1:
        raise RuntimeError(
            "alembic_version contains multiple heads; refusing automatic repair"
        )

    if detected_revision is None:
        if rows:
            print(
                f"REPAIR|existing tracking preserved|version={rows[0]['version_num']}",
                flush=True,
            )
        else:
            print("REPAIR|no base schema detected|left alembic_version empty", flush=True)
        return

    detected_index = REVISION_CHAIN.index(detected_revision)

    if not rows:
        await conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES ($1)",
            detected_revision,
        )
        print(
            f"REPAIR|stamped empty tracking|version={detected_revision}",
            flush=True,
        )
        return

    current = rows[0]["version_num"]
    if current == "0007_seed_starter_nations":
        print(
            "REPAIR|tracking already aligned|version=0007_seed_starter_nations",
            flush=True,
        )
        return

    if current not in REVISION_CHAIN:
        raise RuntimeError(
            f"unknown alembic revision in production: {current}"
        )

    current_index = REVISION_CHAIN.index(current)

    if current_index > detected_index:
        raise RuntimeError(
            f"production is tracked ahead of detected schema: "
            f"tracking={current} detected={detected_revision}"
        )

    if current_index < detected_index:
        await conn.execute("DELETE FROM alembic_version")
        await conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES ($1)",
            detected_revision,
        )
        print(
            f"REPAIR|advanced tracking|from={current}|to={detected_revision}",
            flush=True,
        )
    else:
        print(
            f"REPAIR|tracking already aligned|version={current}",
            flush=True,
        )


async def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("REPAIR|DATABASE_URL is missing", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(database_url)
    try:
        detected = await detect_revision(conn)
        print(
            f"REPAIR|detected_revision={detected or 'base'}",
            flush=True,
        )
        await ensure_version_tracking(conn, detected)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
