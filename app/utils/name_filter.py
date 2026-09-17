"""Trader-name validation and profanity filtering for OPEX MONEY."""

from __future__ import annotations

import re
import unicodedata

# Common English sexual, vulgar, abusive, and common Persian profanity written
# in Finglish. User-facing messages never reveal which rule matched.
BLOCKED_TERMS = frozenset({
    "anal", "anus", "arse", "balls", "bastard", "bitch", "boob", "boobs",
    "bullshit", "cock", "crap", "cum", "dick", "dildo", "fag", "fuck",
    "fucker", "fucking", "goddamn", "horny", "motherfucker", "naked", "nazi",
    "nigger", "nigga", "penis", "piss", "porn", "pussy", "rape", "rapist",
    "sex", "shit", "slut", "tits", "vagina", "whore",
    "kos", "koskesh", "koskes", "kiri", "kir", "kire", "kirm", "koni",
    "kuni", "jende", "jendeh", "madarjende", "pedarjende", "haroom",
    "haramzade", "goh", "gohkhori", "gohkhor", "gohbokhor", "biadab",
    "bikhod", "namahram", "dafe", "dayoos", "dayous",
})

SHORT_EXACT = frozenset({"kos", "kir", "sex", "cum", "goh"})

ASCII_NAME_RE = re.compile(r"^[A-Za-z0-9]{3,15}$")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
DIGIT_RE = re.compile(r"[0-9]")

LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "2": "z", "3": "e", "4": "a",
    "5": "s", "6": "g", "7": "t", "8": "b", "9": "g",
})


def normalize_name(value: str) -> str:
    """Normalize case, Unicode compatibility, separators and common leetspeak."""
    value = unicodedata.normalize("NFKC", value).strip().lower().translate(LEET_MAP)
    return NON_ALNUM_RE.sub("", value)


def _letters_only(value: str) -> str:
    """Remove numeric characters after leetspeak normalization."""
    return DIGIT_RE.sub("", normalize_name(value))


def _raw_letters_only(value: str) -> str:
    """Remove digits without interpreting them, catching suffix/prefix digits."""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"[^a-z]", "", value)


def is_valid_trader_name(value: str) -> bool:
    """Accept only 3-15 ASCII English letters/digits, no spaces or symbols."""
    return bool(ASCII_NAME_RE.fullmatch(value.strip()))


def is_blocked_trader_name(value: str) -> bool:
    """Detect direct profanity and common digit/leet obfuscation."""
    raw = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = normalize_name(value)
    letters = _letters_only(value)
    raw_letters = _raw_letters_only(value)
    if not normalized:
        return False

    if raw in BLOCKED_TERMS or normalized in BLOCKED_TERMS:
        return True

    # Exact short-term checks prevent false positives such as legitimate names
    # that merely contain a short sequence.
    if letters in SHORT_EXACT or raw_letters in SHORT_EXACT:
        return True

    # Detect a blocked word after digits have been inserted between its letters.
    for term in BLOCKED_TERMS:
        if len(term) >= 4 and (letters == term or raw_letters == term):
            return True

    # Longer roots may be embedded in a compound trader name.
    for term in BLOCKED_TERMS:
        if len(term) >= 6 and term in letters:
            return True

    return False
