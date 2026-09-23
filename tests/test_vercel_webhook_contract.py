from pathlib import Path


def test_vercel_webhook_runtime_uses_redis_and_aiogram_dispatcher():
    source = Path("app/bot_webhook.py").read_text(encoding="utf-8")
    assert "RedisStorage.from_url(settings.redis_url)" in source
    assert "dp = Dispatcher(storage=storage)" in source
    assert "dp.include_router(start_router)" in source
    assert "dp.include_router(market_router)" in source


def test_telegram_webhook_endpoint_contract_is_present():
    source = Path("admin/main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/telegram/webhook"' in source
    assert "X-Telegram-Bot-Api-Secret-Token" in source
    assert '@app.post("/api/telegram/setup"' in source


def test_webhook_is_not_polling_runtime():
    source = Path("app/bot_webhook.py").read_text(encoding="utf-8")
    assert "start_polling" not in source
    assert "run_polling" not in source
