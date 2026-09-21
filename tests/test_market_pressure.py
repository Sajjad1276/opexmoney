from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.market.market_pressure import record_trade_pressure


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    def _hash(self, key: str) -> dict[str, str]:
        return self.hashes.setdefault(key, {})

    async def hincrbyfloat(self, key: str, field: str, amount: str):
        current = Decimal(self._hash(key).get(field, "0"))
        self._hash(key)[field] = str(current + Decimal(amount))

    async def hincrby(self, key: str, field: str, amount: int):
        current = int(self._hash(key).get(field, "0"))
        self._hash(key)[field] = str(current + int(amount))

    async def hset(self, key: str, field: str, value: str):
        self._hash(key)[field] = value

    async def ttl(self, key: str):
        return self.ttls.get(key, -2)

    async def expire(self, key: str, seconds: int):
        self.ttls[key] = seconds
        self.expire_calls.append((key, seconds))
        return True


@pytest.mark.asyncio
async def test_trade_pressure_accumulates_and_refreshes_rolling_ttl(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(
        "app.core.redis.get_redis",
        lambda: redis,
    )

    await record_trade_pressure(
        nation_id=7,
        side="buy",
        volume=Decimal("100.50"),
        buyer_home_nation_id=7,
    )
    await record_trade_pressure(
        nation_id=7,
        side="buy",
        volume=Decimal("20.25"),
        buyer_home_nation_id=99,
    )
    await record_trade_pressure(
        nation_id=7,
        side="sell",
        volume=Decimal("30.75"),
    )

    fields = redis.hashes["pressure:7"]
    assert Decimal(fields["buy_volume"]) == Decimal("120.75")
    assert Decimal(fields["sell_volume"]) == Decimal("30.75")
    assert Decimal(fields["foreign_buy"]) == Decimal("20.25")
    assert int(fields["tx_count"]) == 3
    assert "last_update" in fields
    assert redis.ttls["pressure:7"] == 900
    assert redis.expire_calls == [
        ("pressure:7", 900),
        ("pressure:7", 900),
        ("pressure:7", 900),
    ]
