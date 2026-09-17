"""Trader-name validation and profanity filtering for OPEX MONEY."""

from __future__ import annotations

import re
import unicodedata

# Common English sexual, vulgar, abusive, and otherwise inappropriate names.
# User-facing messages never reveal which blocked term matched.
BLOCKED_TERMS = frozenset(
    {
        "anal",
        "anus",
        "arse",
        "balls",
        "bastard",
        "bitch",
        "boob",
        "boobs",
        "bullshit",
        "cock",
        "crap",
        "cum",
        "dick",
        "dildo",
        "fag",
        "fuck",
        "fucker",
        "fucking",
        "goddamn",
        "horny",
        "motherfucker",
        "naked",
        "nazi",
        "nigger",
        "nigga",
        "penis",
        "piss",
        "porn",
        "pussy",
        "rape",
        "rapist",
        "sex",
        "shit",
        "slut",
        "tits",
        "vagina",
        "whore",
    }
)

SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
ASCII_NAME_RE = re.compile(r"^[A-Za-z]{3,15}$")


def normalize_name(value: str) -> str:
    """Normalize a candidate for reliable profanity checks."""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    return SEPARATOR_RE.sub("", value)


def is_valid_trader_name(value: str) -> bool:
    """Return True only for 3-15 ASCII English letters."""
    return bool(ASCII_NAME_RE.fullmatch(value.strip()))


def is_blocked_trader_name(value: str) -> bool:
    """Detect blocked terms while avoiding short-word false positives."""
    raw = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = normalize_name(value)
    if not normalized:
        return False

    # Exact matching handles normal valid names. The normalized check handles
    # punctuation-separated variants such as f.u.c.k, even though those names
    # are rejected by the format validator as well.
    return raw in BLOCKED_TERMS or normalized in BLOCKED_TERMS
