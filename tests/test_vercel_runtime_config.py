from __future__ import annotations

from config import Settings


def test_upstash_rest_credentials_become_tls_redis_url(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(
        bot_token="123:token",
        database_url="postgresql://example",
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="secret/token",
    )

    assert settings.redis_url == (
        "rediss://default:secret%2Ftoken@example.upstash.io:6379"
    )
    assert settings.telegram_webhook_secret
    assert settings.telegram_webhook_setup_token
