from __future__ import annotations

import asyncio
import os

from redis.asyncio import Redis


async def main() -> None:
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        keys = [key async for key in redis.scan_iter(match="pressure:*", count=500)]
        if keys:
            await redis.delete(*keys)
        print(f"PRESSURE_RESET|deleted_keys={len(keys)}", flush=True)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
