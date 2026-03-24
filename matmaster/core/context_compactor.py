"""ContextCompactor -- runtime context compression for the agent kernel.

Compresses old messages via LLM summarization when the estimated prompt
tokens approach the context window limit. Falls back to sliding-window
truncation if summarization fails.
"""

from __future__ import annotations

import json
import logging

from matmaster.types.messages import Message

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    """Lazy-load tiktoken encoder with fallback."""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken

        _encoder = tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        logger.warning("tiktoken unavailable, using len/4 heuristic")
        _encoder = None
    return _encoder


def estimate_tokens(messages: list[Message], safety_margin: float = 1.0) -> int:
    """Estimate token count for a list of messages."""
    total = 0
    enc = _get_encoder()
    for msg in messages:
        text = json.dumps(msg.to_api_dict(), ensure_ascii=False)
        if enc is not None:
            total += len(enc.encode(text))
        else:
            total += max(len(text) // 4, 1)
        total += 4
    return int(total * safety_margin)
