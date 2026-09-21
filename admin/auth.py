from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import unquote

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from admin.dependencies import get_redis


logger = logging.getLogger("opex.admin.auth")


class AdminUser(BaseModel):
    user_id: int
    username: str


class TooManyRequests(Exception):
    pass


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_USER_IDS: str = ""
    DATABASE_URL: str
    REDIS_URL: str
    AI_ENABLED: bool = False
    AI_MODEL: str = ""
    GEMINI_API_KEY: str = ""

    @computed_field
    @property
    def admin_ids(self) -> list[int]:
        if not self.ADMIN_USER_IDS:
            return []
        return [
            int(x.strip())
            for x in self.ADMIN_USER_IDS.split(",")
            if x.strip()
        ]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def validate_init_data(init_data_raw: str, bot_token: str) -> dict:
    decoded = unquote(init_data_raw)
    parts = decoded.split("&")

    pairs: list[tuple[str, str]] = []
    received_hash: str | None = None
    auth_date_raw: str | None = None

    for part in parts:
        if "=" not in part:
            raise ValueError("Invalid initData")
        key, value = part.split("=", 1)
        if key == "hash":
            received_hash = value
            continue
        pairs.append((key, value))
        if key == "auth_date":
            auth_date_raw = value

    if not received_hash:
        raise ValueError("Invalid hash")

    if auth_date_raw is None:
        raise ValueError("Invalid initData")

    try:
        auth_date = int(auth_date_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid initData") from exc

    if time.time() - auth_date > 86400:
        raise ValueError("initData expired")

    sorted_pairs = sorted(pairs, key=lambda item: item[0])
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted_pairs
    )

    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()

    expected = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("Invalid hash")

    user_raw = None
    for key, value in pairs:
        if key == "user":
            user_raw = value
            break

    if not user_raw:
        raise ValueError("Invalid user data")

    try:
        user_data = json.loads(user_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid user data") from exc

    if not isinstance(user_data, dict):
        raise ValueError("Invalid user data")

    return user_data


def check_is_admin(user_data: dict) -> int:
    try:
        user_id = int(user_data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PermissionError("Forbidden") from exc

    if user_id not in settings.admin_ids:
        raise PermissionError(f"User {user_id} is not an admin")

    return user_id


async def check_rate_limit(user_id: int, redis: Redis) -> None:
    key = f"admin:ratelimit:{user_id}"

    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > 120:
            raise TooManyRequests()
    except TooManyRequests:
        raise
    except Exception:
        logger.warning(
            "Admin rate limiting is unavailable; allowing request.",
            exc_info=True,
        )


def _is_dev_mode() -> bool:
    return os.getenv("DEV_MODE", "").strip().lower() == "true"


async def get_admin_user(
    request: Request,
    redis: Redis = Depends(get_redis),
) -> AdminUser:
    try:
        dev_admin_id_raw = request.headers.get("X-Dev-Admin-Id")

        if _is_dev_mode() and dev_admin_id_raw is not None:
            try:
                dev_admin_id = int(dev_admin_id_raw)
            except (TypeError, ValueError):
                dev_admin_id = None

            if (
                dev_admin_id is not None
                and dev_admin_id in settings.admin_ids
            ):
                await check_rate_limit(dev_admin_id, redis)
                return AdminUser(user_id=dev_admin_id, username="")

        init_data = request.headers.get("X-Telegram-Init-Data")
        if not init_data:
            raise HTTPException(
                status_code=401,
                detail="Missing authentication",
            )

        try:
            user_data = validate_init_data(init_data, settings.BOT_TOKEN)
        except ValueError as exc:
            raise HTTPException(
                status_code=401,
                detail=str(exc),
            ) from exc

        try:
            user_id = check_is_admin(user_data)
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail="Forbidden",
            ) from exc

        try:
            await check_rate_limit(user_id, redis)
        except TooManyRequests as exc:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
            ) from exc

        return AdminUser(
            user_id=user_id,
            username=str(user_data.get("username", "")),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected admin authentication error.")
        raise HTTPException(
            status_code=500,
            detail="Authentication error",
        ) from None


# ── END OF auth.py ──
