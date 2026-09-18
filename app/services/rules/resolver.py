from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RuleOverride
from app.services.rules.registry import RULE_REGISTRY, clamp_rule_value, get_rule

_CACHE_TTL_SECONDS = 30.0
_CACHE: dict[tuple[str, int | None, int | None], tuple[float, Decimal | int | bool]] = {}


def _cache_key(key: str, nation_id: int | None, player_id: int | None) -> tuple[str, int | None, int | None]:
    return key, nation_id, player_id


def invalidate_rule_cache(key: str | None = None) -> None:
    if key is None:
        _CACHE.clear()
        return

    for cache_key in tuple(_CACHE):
        if cache_key[0] == key:
            _CACHE.pop(cache_key, None)


def _is_active(override: RuleOverride, now: datetime) -> bool:
    if not override.is_active:
        return False
    if override.active_from > now:
        return False
    if override.active_until is not None and override.active_until <= now:
        return False
    if override.suspended_until is not None and override.suspended_until > now:
        return False
    return True


def _choose_override(
    overrides: list[RuleOverride],
    *,
    nation_id: int | None,
    player_id: int | None,
) -> RuleOverride | None:
    candidates = []

    if player_id is not None:
        candidates.extend(
            item for item in overrides
            if item.scope == "player" and item.target_id == player_id
        )
    if nation_id is not None:
        candidates.extend(
            item for item in overrides
            if item.scope == "nation" and item.target_id == nation_id
        )
    candidates.extend(item for item in overrides if item.scope == "global" and item.target_id is None)

    if not candidates:
        return None

    priority = {"player": 3, "nation": 2, "global": 1}
    candidates.sort(
        key=lambda item: (priority.get(item.scope, 0), item.active_from, item.id),
        reverse=True,
    )
    return candidates[0]


async def resolve(
    session: AsyncSession,
    key: str,
    *,
    nation_id: int | None = None,
    player_id: int | None = None,
) -> Decimal | int | bool:
    rule = get_rule(key)
    cache_key = _cache_key(key, nation_id, player_id)
    now_monotonic = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and now_monotonic - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    now = datetime.utcnow()
    result = await session.execute(
        select(RuleOverride)
        .where(RuleOverride.rule_key == key)
        .order_by(RuleOverride.active_from.desc(), RuleOverride.id.desc())
    )
    overrides = [item for item in result.scalars().all() if _is_active(item, now)]
    selected = _choose_override(overrides, nation_id=nation_id, player_id=player_id)

    if selected is None:
        resolved = clamp_rule_value(rule.default_value, rule)
    else:
        resolved = clamp_rule_value(selected.value, rule)

    _CACHE[cache_key] = (now_monotonic, resolved)
    return resolved


def cache_size() -> int:
    return len(_CACHE)


def registry_defaults() -> dict[str, Any]:
    return {key: rule.default_value for key, rule in RULE_REGISTRY.items()}
