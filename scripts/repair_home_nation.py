from __future__ import annotations

import asyncio
import logging

from sqlalchemy import exists, select

from app.database.models import NationMember, User
from app.database.session import async_session

logger = logging.getLogger("opexmoney.repair_home_nation")


async def repair_home_nation_sync() -> int:
    """
    Clear home_nation_id for users whose home is not backed by an active
    NationMember row.

    This is intentionally a one-time data repair. It does not create
    memberships or alter NationMember rows.
    """
    repaired = 0

    async with async_session() as session:
        async with session.begin():
            active_member_exists = exists().where(
                NationMember.user_id == User.user_id,
                NationMember.nation_id == User.home_nation_id,
                NationMember.is_active.is_(True),
            )
            result = await session.execute(
                select(User)
                .where(
                    User.home_nation_id.is_not(None),
                    ~active_member_exists,
                )
                .with_for_update()
            )
            users = list(result.scalars().all())

            for user in users:
                old_nation_id = user.home_nation_id
                # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
                user.home_nation_id = None
                repaired += 1
                logger.warning(
                    "REPAIR|home_nation_sync|user_id=%s|cleared_home_nation_id=%s",
                    user.user_id,
                    old_nation_id,
                )

    logger.info("REPAIR|home_nation_sync|repaired=%s", repaired)
    return repaired


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    repaired = await repair_home_nation_sync()
    print(f"REPAIR|home_nation_sync|repaired={repaired}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
