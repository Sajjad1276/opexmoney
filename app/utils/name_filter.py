"""Trader-name validation and profanity filtering for OPEX MONEY."""

from __future__ import annotations

import re
import unicodedata

# Canonical roots cover common English profanity and common Persian profanity
# written in Finglish. The UI never reveals which rule matched.
BLOCKED_TERMS = frozenset({
    "anal", "anus", "arse", "balls", "bastard", "bitch", "boob", "boobs",
    "bullshit", "cock", "crap", "cum", "dick", "dildo", "fag", "fuck",
    "fucker", "fucking", "goddamn", "horny", "motherfucker", "naked", "nazi",
    "nigger", "nigga", "penis", "piss", "porn", "pussy", "rape", "rapist",
    "sex", "shit", "slut", "tits", "vagina", "whore",
    "kos", "koskesh", "koskes", "kiri", "kir", "kire", "kirm", "koni",
    "kuni", "jende", "jendeh", "jendeh", "madarjende", "pedarjende",
    "haroom", "haramzade", "goh", "gohkhori", "gohkhor", "gohbokhor",
    "biadab", "bikhod", "namahram", "dafe", "dayoos", "dayous",
})

# Terms which are short enough that substring matching would create too many
# false positives in legitimate English names.
SHORT_EXACT = frozenset({"kos", "kir", "sex", "cum", "goh"})

ASCII_NAME_RE = re.compile(r"^[A-Za-z0-9]{3,15}$")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
DIGIT_RE = re.compile(r"[0-9]")

# Controlled leetspeak map. Digits are removed after common substitutions so
# names such as k0s and k1r can be detected without accepting punctuation.
LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "2": "z", "3": "e", "4": "a",
    "5": "s", "6": "g", "7": "t", "8": "b", "9": "g",
})


def normalize_name(value: str) -> str:
    """Normalize case, Unicode compatibility, separators and common leetspeak."""
    value = unicodedata.normalize("NFKC", value).strip().lower().translate(LEET_MAP)
    return NON_ALNUM_RE.sub("", value)


def _letters_only(value: str) -> str:
    """Remove digits after leetspeak normalization for digit-insertion attacks."""
    return DIGIT_RE.sub("", normalize_name(value))


def is_valid_trader_name(value: str) -> bool:
    """Accept only 3-15 ASCII letters/digits, with no spaces or symbols."""
    return bool(ASCII_NAME_RE.fullmatch(value.strip()))


def is_blocked_trader_name(value: str) -> bool:
    """Detect direct and digit-obfuscated profanity without broad false positives."""
    raw = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = normalize_name(value)
    letters = _letters_only(value)
    if not normalized:
        return False

    if raw in BLOCKED_TERMS or normalized in BLOCKED_TERMS:
        return True

    # Check the spelling after digits are removed. This catches forms such as
    # k0s, k1r and k0sk3sh while preserving the original username format.
    for term in BLOCKED_TERMS:
        if len(term) < 4:
            continue
        if letters == term:
            return True

    # Longer Persian/Finglish roots may occur inside a compound username.
    for term in BLOCKED_TERMS:
        if len(term) >= 6 and term in letters:
            return True

    return False
