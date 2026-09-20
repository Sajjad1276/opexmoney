from __future__ import annotations

from app.services.ai_world_data import (
    AI_USER_ID_BASE,
    AI_WORLD_SEEDS,
    NATION_NAMES,
    PLAYER_NAMES,
)


def test_ai_world_has_exactly_100_seed_agents() -> None:
    assert len(NATION_NAMES) == 100
    assert len(PLAYER_NAMES) == 100
    assert len(AI_WORLD_SEEDS) == 100
    assert len({seed.name for seed in AI_WORLD_SEEDS}) == 100
    assert len({seed.currency_code for seed in AI_WORLD_SEEDS}) == 100
    assert len({seed.player_name for seed in AI_WORLD_SEEDS}) == 100
    assert len({seed.player_id for seed in AI_WORLD_SEEDS}) == 100


def test_ai_world_uses_safe_database_identity_range() -> None:
    assert AI_USER_ID_BASE + 100 < 9_223_372_036_854_775_807
    assert all(seed.player_id > 0 for seed in AI_WORLD_SEEDS)
