"""Helpers for filtering punctuation-only response preambles."""

from __future__ import annotations

import re

_TRIVIAL_RESPONSE_RE = re.compile(r'^[\s.。…·\-—_*]+$')


def is_trivial_response_text(text: str | None) -> bool:
    """Return True for punctuation-only response fragments such as ``...``."""
    if not text:
        return False
    return _TRIVIAL_RESPONSE_RE.match(text) is not None
