from __future__ import annotations

from app.database.models import Nation


def require_human_nation(nation: Nation) -> int:
    if nation.is_ai:
        raise ValueError("AI nations do not use Telegram groups.")
    if nation.group_id is None:
        raise ValueError("Human nations must have a Telegram group.")
    return nation.group_id


def require_ai_nation(nation: Nation) -> None:
    if not nation.is_ai:
        raise ValueError("This nation is human-backed.")
    if nation.group_id is not None:
        raise ValueError("AI nations must not have a Telegram group.")
