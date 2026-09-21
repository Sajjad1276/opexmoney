from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import quote

import pytest

from admin.auth import get_admin_user, validate_telegram_init_data
from fastapi import HTTPException


def build_init_data(bot_token: str, user_id: int) -> str:
    user = json.dumps({"id": user_id, "first_name": "Admin"}, separators=(",", ":"))
    raw = {
        "auth_date": "1760000000",
        "user": user,
        "query_id": "AAH_test",
    }
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(raw.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()
    digest = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return "&".join(
        [
            f"auth_date={quote(raw['auth_date'])}",
            f"user={quote(raw['user'])}",
            f"query_id={quote(raw['query_id'])}",
            f"hash={digest}",
        ]
    )


def test_valid_admin_init_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_USER_IDS", "12345,67890")

    init_data = build_init_data("test-token", 12345)

    assert validate_telegram_init_data(init_data) == 12345


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.replace("hash=", "hash=0"),
        lambda value: value.replace("user=%7B", "user=%7B%22id%22%3A99999,%22x%22%3A1,%22orig%22%3A%7B"),
    ],
)
def test_invalid_admin_init_data(monkeypatch: pytest.MonkeyPatch, mutator) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_USER_IDS", "12345")

    init_data = build_init_data("test-token", 12345)

    with pytest.raises(HTTPException) as exc_info:
        validate_telegram_init_data(mutator(init_data))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_header_dependency() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_admin_user(None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing auth"
