"""Helpers for filtering non-visible assistant response text."""

from __future__ import annotations

import re

_TRIVIAL_RESPONSE_RE = re.compile(r'^[\s.。…·\-—_*]+$')
_EMPTY_RESPONSE_SENTINELS = frozenset({"none", "null"})


def is_trivial_response_text(text: str | None) -> bool:
    """Return True for punctuation-only response fragments such as ``...``."""
    if not text:
        return False
    return _TRIVIAL_RESPONSE_RE.match(text) is not None


def normalize_visible_response_text(text: str | None) -> str | None:
    """Return user-visible assistant text, or None for empty-value sentinels."""
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None
    if stripped.casefold() in _EMPTY_RESPONSE_SENTINELS:
        return None
    return text


def is_empty_response_sentinel_prefix(text: str | None) -> bool:
    """Return True while streamed text could still become an empty sentinel."""
    if text is None:
        return True

    stripped = text.strip()
    if not stripped:
        return True

    folded = stripped.casefold()
    return any(sentinel.startswith(folded) for sentinel in _EMPTY_RESPONSE_SENTINELS)
