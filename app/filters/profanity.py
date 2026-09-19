"""Profanity filter adapter used by onboarding."""

from __future__ import annotations

from app.utils.name_filter import is_blocked_trader_name


def profanity_filter(value: str) -> bool:
    """Return True when the trader name is blocked by the existing filter service."""
    return is_blocked_trader_name(value)
