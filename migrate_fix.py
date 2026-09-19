"""One-off production fix for alembic_version tracking.

The production database already has the tables created by migrations
0001-0004 (applied manually or via a prior deployment), but the
alembic_version table never recorded that fact. As a result,
`alembic upgrade head` tries to re-run migration 0001 and fails with
DuplicateTableError because e.g. the "nations" table already exists.

This script stamps alembic_version with the revisions 0001-0004 so that
the next `alembic upgrade head` run picks up cleanly at 0005 onward. It
does NOT touch any application tables and does NOT invoke alembic itself
-- that happens afterwards, in the preDeployCommand.

Usage: python migrate_fix.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate_fix")

# The revisions we know are already applied on production but are missing
# from alembic_version. These correspond to files in alembic/versions/.
EXPECTED_REVISION_PREFIXES = ("0001", "0002", "0003", "0004")

VERSIONS_DIR = Path(__file__).resolve().parent / "alembic" / "versions"
REVISION_RE = re.compile(r"""revision\s*=\s*["']([^"']+)["']""")


def _to_asyncpg_dsn(database_url: str) -> str:
    """Normalize DATABASE_URL into a DSN that asyncpg.connect() accepts."""
    dsn = database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    return dsn


def _discover_target_revisions() -> list[str]:
    """Read alembic/versions/ and extract the revision ids for 0001-0004."""
    if not VERSIONS_DIR.is_dir():
        logger.error("Migrations directory not found: %s", VERSIONS_DIR)
        raise SystemExit(1)

    revisions: dict[str, str] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        prefix = path.name.split("_", 1)[0]
        if prefix not in EXPECTED_REVISION_PREFIXES:
            continue
        contents = path.read_text(encoding="utf-8")
        match = REVISION_RE.search(contents)
        if not match:
            logger.warning("Could not find revision id in %s, skipping", path.name)
            continue
        revisions[prefix] = match.group(1)
        logger.info("Discovered migration file %s -> revision %s", path.name, match.group(1))

    ordered = [revisions[prefix] for prefix in EXPECTED_REVISION_PREFIXES if prefix in revisions]
    if len(ordered) != len(EXPECTED_REVISION_PREFIXES):
        found = set(revisions)
        missing = [p for p in EXPECTED_REVISION_PREFIXES if p not in found]
        logger.error("Missing expected migration files for prefixes: %s", missing)
        raise SystemExit(1)

    return ordered


async def _ensure_alembic_version_table(conn: asyncpg.Connection) -> None:
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
            AND table_name = 'alembic_version'
        )
        """
    )
    if exists:
        logger.info("alembic_version table already exists")
        return

    logger.info("alembic_version table not found, creating it")
    await conn.execute(
        """
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL,
            CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
        )
        """
    )
    logger.info("Created alembic_version table")


async def _stamp_revisions(conn: asyncpg.Connection, revisions: list[str]) -> None:
    existing_rows = await conn.fetch("SELECT version_num FROM alembic_version")
    existing = {row["version_num"] for row in existing_rows}
    logger.info("Existing alembic_version rows: %s", sorted(existing) or "<none>")

    for revision in revisions:
        if revision in existing:
            logger.info("Revision %s already tracked, skipping", revision)
            continue
        logger.info("Stamping missing revision %s", revision)
        await conn.execute(
            """
            INSERT INTO alembic_version (version_num)
            VALUES ($1)
            ON CONFLICT (version_num) DO NOTHING
            """,
            revision,
        )


async def _print_final_state(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch("SELECT version_num FROM alembic_version ORDER BY version_num")
    logger.info("Final alembic_version table state:")
    if not rows:
        logger.info("  <empty>")
    for row in rows:
        logger.info("  - %s", row["version_num"])


async def main() -> None:
    load_dotenv()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        raise SystemExit(1)

    dsn = _to_asyncpg_dsn(database_url)
    target_revisions = _discover_target_revisions()
    logger.info("Target revisions to ensure are stamped: %s", target_revisions)

    logger.info("Connecting to database")
    conn = await asyncpg.connect(dsn=dsn)
    try:
        async with conn.transaction():
            await _ensure_alembic_version_table(conn)
            await _stamp_revisions(conn, target_revisions)
        await _print_final_state(conn)
    finally:
        await conn.close()
        logger.info("Database connection closed")

    logger.info("migrate_fix.py completed successfully")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception:
        logger.exception("migrate_fix.py failed")
        sys.exit(1)
