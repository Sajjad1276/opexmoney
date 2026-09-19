"""Parse and validate AI responses before Telegram delivery."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_ALLOWED_OPEN_TAGS = {"b", "u"}
_ALLOWED_TAG_PATTERN = re.compile(
    r"</?\s*([A-Za-z][A-Za-z0-9]*)[^>]*>",
    re.IGNORECASE,
)
_FENCED_JSON_PATTERN = re.compile(
    r"\x60{3}(?:json)?\s*(.*?)\s*\x60{3}",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ParsedAIResponse:
    """Validated response ready for Telegram."""

    reply: str


def _strip_unsupported_html(text: str) -> str:
    """Keep only the two Telegram tags allowed by the personality contract."""
    def replace(match: re.Match[str]) -> str:
        tag = match.group(1).lower()
        if tag in _ALLOWED_OPEN_TAGS:
            raw = match.group(0)
            closing = raw.lstrip().startswith("</")
            return f"</{tag}>" if closing else f"<{tag}>"
        return ""

    return _ALLOWED_TAG_PATTERN.sub(replace, text)


def _normalize_lines(text: str) -> str:
    """Enforce the maximum four-line contract."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]

    if not lines:
        raise ValueError("AI returned an empty reply")

    if len(lines) > 4:
        lines = lines[:3] + [" ".join(lines[3:])]

    normalized = "\n".join(lines).strip()

    if len(normalized) > 700:
        normalized = normalized[:697].rstrip() + "..."

    return normalized


def _extract_reply(raw: str) -> str:
    """Extract the reply from JSON, fenced JSON, or plain text."""
    candidate = raw.strip()
    if not candidate:
        raise ValueError("AI returned an empty payload")

    fenced = _FENCED_JSON_PATTERN.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        value = parsed.get("reply")
        if isinstance(value, str):
            return value.strip()

    if isinstance(parsed, str):
        return parsed.strip()

    if candidate.startswith("{"):
        raise ValueError("AI JSON does not contain a valid reply field")

    return candidate


def parse_ai_response(raw: str) -> ParsedAIResponse:
    """Validate, sanitize and normalize an AI response."""
    reply = _extract_reply(raw)
    reply = _strip_unsupported_html(reply)
    reply = _normalize_lines(reply)

    if not reply:
        raise ValueError("AI reply became empty after sanitization")

    return ParsedAIResponse(reply=reply)


__all__ = ["ParsedAIResponse", "parse_ai_response"]