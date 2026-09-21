from __future__ import annotations

import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException


UNAUTHORIZED = "Unauthorized"
MISSING_AUTH = "Missing auth"


def _admin_user_ids() -> set[int]:
    raw = os.getenv("ADMIN_USER_IDS", "")
    result: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError:
            continue
    return result


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=UNAUTHORIZED)


def validate_telegram_init_data(init_data: str) -> int:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise _unauthorized()

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        received_hash = data.pop("hash", None)
        if not received_hash:
            raise _unauthorized()

        data_check_string = "\\n".join(
            f"{key}={value}" for key, value in sorted(data.items())
        )
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=bot_token.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, received_hash):
            raise _unauthorized()

        raw_user = data.get("user")
        if not raw_user:
            raise _unauthorized()
        user = json.loads(raw_user)
        user_id = int(user["id"])
    except HTTPException:
        raise
    except Exception as exc:
        raise _unauthorized() from exc

    if user_id not in _admin_user_ids():
        raise _unauthorized()

    return user_id


async def get_admin_user(
    init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> int:
    if not init_data:
        raise HTTPException(status_code=401, detail=MISSING_AUTH)
    return validate_telegram_init_data(init_data)
