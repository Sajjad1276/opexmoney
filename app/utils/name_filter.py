"""Trader-name validation and profanity filtering for OPEX MONEY."""

from __future__ import annotations

import re
import unicodedata

# The trader name is restricted to ASCII letters, so this list targets
# common English sexual, vulgar, abusive, and otherwise inappropriate terms.
# Keep the user-facing response generic instead of echoing the blocked term.
BLOCKED_TERMS = frozenset(
    {
        "anal",
        "anus",
        "arse",
        "ass",
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
        "tit",
        "tits",
        "vagina",
        "whore",
    }
)

# Prevent trivial separator insertion such as f.u.c.k or f-u-c-k.
SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
ASCII_NAME_RE = re.compile(r"^[A-Za-z]{3,15}$")


def normalize_name(value: str) -> str:
    """Normalize a candidate for reliable duplicate/profanity checks."""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    return SEPARATOR_RE.sub("", value)


def is_valid_trader_name(value: str) -> bool:
    """Return True only for 3-15 ASCII English letters."""
    return bool(ASCII_NAME_RE.fullmatch(value.strip()))


def is_blocked_trader_name(value: str) -> bool:
    """Detect blocked words, including simple separator/punctuation bypasses."""
    normalized = normalize_name(value)
    if not normalized:
        return False
    return any(term in normalized for term in BLOCKED_TERMS)
