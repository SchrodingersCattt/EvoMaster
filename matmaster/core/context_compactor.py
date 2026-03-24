"""ContextCompactor -- runtime context compression for the agent kernel.

Compresses old messages via LLM summarization when the estimated prompt
tokens approach the context window limit. Falls back to sliding-window
truncation if summarization fails.
"""

from __future__ import annotations

import json
import logging

from matmaster.types.messages import (
    AssistantMessage,
    Message,
    ToolMessage,
    UserMessage,
)

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


def _find_initial_task_index(messages: list[Message]) -> int:
    """Find the index of the initial task UserMessage."""
    first_assistant = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, AssistantMessage):
            first_assistant = i
            break
    if first_assistant == -1:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], UserMessage):
                return i
        return -1
    for i in range(first_assistant - 1, -1, -1):
        if isinstance(messages[i], UserMessage):
            return i
    return -1


def parse_turns(messages: list[Message]) -> list[list[Message]]:
    """Parse mutable messages into complete turns."""
    task_idx = _find_initial_task_index(messages)
    if task_idx == -1:
        return []
    start = task_idx + 1
    if start >= len(messages):
        return []

    turns: list[list[Message]] = []
    current_turn: list[Message] = []
    current_has_assistant = False

    for msg in messages[start:]:
        if isinstance(msg, AssistantMessage):
            if current_turn and current_has_assistant:
                turns.append(current_turn)
                current_turn = []
            current_turn.append(msg)
            current_has_assistant = True
            continue

        if isinstance(msg, UserMessage):
            if current_turn and current_has_assistant:
                turns.append(current_turn)
                current_turn = []
                current_has_assistant = False
            current_turn.append(msg)
            continue

        if isinstance(msg, ToolMessage):
            current_turn.append(msg)
            continue

        current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return turns
