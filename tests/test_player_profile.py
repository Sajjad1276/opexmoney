from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.handlers.profile import _bar, build_profile_text
from app.services.player_profile_service import PlayerProfile


def _profile(**overrides):
    values = dict(
        username="TestTrader",
        nation_name="Avalon",
        nation_currency="AVL",
        wealth_xr=Decimal("1234.50"),
        trades=12,
        trade_volume=Decimal("4200"),
        governance_actions=4,
        lessons_completed=2,
        wars_seen=1,
        archetype="معامله‌گر",
        reputation_trade=82,
        reputation_governance=40,
        reputation_knowledge=25,
        reputation_military=15,
        reputation_builder=10,
    )
    values.update(overrides)
    return PlayerProfile(**values)


def test_profile_bar_clamps_to_valid_width() -> None:
    assert _bar(0) == "░░░░░░░░░░"
    assert _bar(100) == "██████████"
    assert len(_bar(-10)) == 10
    assert len(_bar(150)) == 10


def test_profile_text_contains_derived_identity_and_real_metrics() -> None:
    text = build_profile_text(_profile())

    assert "TestTrader" in text
    assert "Avalon · AVL" in text
    assert "معامله‌گر" in text
    assert "12" in text
    assert "4200.00" in text
    assert "شهرت‌های رفتاری" in text


def test_profile_text_supports_players_without_nation() -> None:
    text = build_profile_text(
        _profile(
            nation_name=None,
            nation_currency=None,
        )
    )

    assert "بدون ملت" in text
