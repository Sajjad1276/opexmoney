from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
ROUTER = (ROOT / "admin/routers/panel_sections.py").read_text(encoding="utf-8")
ALERT_SERVICE = (ROOT / "app/services/alert_service.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "alembic/versions/0022_admin_price_alert_active.py").read_text(encoding="utf-8")


def test_economy_behavior_snapshot_contract():
    required = [
        "data-economy-tab=\"behavior\"",
        "/api/economy/behavior-snapshots?limit=48",
        "بازیکنان فعال",
        "حجم معاملات",
        "Gini",
        "سهم ۱۰٪",
        "برابری خوب",
        "نابرابری بالا",
        "آخرین بروزرسانی:",
        "economy-behavior-refresh",
    ]
    for token in required:
        assert token in INDEX


def test_market_price_alert_contract():
    required = [
        "data-market-tab=\"alerts\"",
        "Price Alerts",
        "تاریخچه نرخ",
        "/api/market/price-alerts",
        "/api/market/price-alerts/",
        "غیرفعال کردن",
        "هشدارهای فعال",
        "هشدارهای Trigger شده",
        "مقدار هنگام trigger",
    ]
    for token in required:
        assert token in INDEX

    assert '@router.get("/api/economy/behavior-snapshots")' in ROUTER
    assert '@router.get("/api/market/price-alerts")' in ROUTER
    assert '@router.patch("/api/market/price-alerts/{alert_id}/toggle")' in ROUTER
    assert "PriceAlert.triggered_value" in ROUTER


def test_price_alert_state_is_persisted_and_checked():
    assert "is_active" in MIGRATION
    assert "triggered_value" in MIGRATION
    assert "PriceAlert.is_active.is_(True)" in ALERT_SERVICE
    assert "alert.triggered_at = datetime.now(UTC)" in ALERT_SERVICE
    assert "alert.triggered_value = current" in ALERT_SERVICE
