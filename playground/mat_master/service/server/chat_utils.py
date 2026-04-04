"""Local chat utilities for the playground service.

Provides simple implementations of helpers that were previously in the
production backend (src/services/chat_history, src/utils/chat_event_source).
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Event source normalization
# ---------------------------------------------------------------------------

_AGENT_NAME_RE = re.compile(r'^(agent[_\-]?\d+|default|general|assistant)$', re.IGNORECASE)


def normalize_event_source(source: Any) -> str:
    """Normalize an agent source name to a clean display string.

    Maps generic agent names ('default', 'agent_0', etc.) to 'MatMaster'.
    Returns other sources as-is (trimmed).
    """
    if source is None:
        return 'MatMaster'
    s = str(source).strip()
    if not s or _AGENT_NAME_RE.match(s):
        return 'MatMaster'
    return s


# ---------------------------------------------------------------------------
# Chat history utilities
# ---------------------------------------------------------------------------

_SPAWN_PREFIXES = ('spawn_', 'subagent', 'sub_agent', 'SubAgent')


class ChatHistoryConverter:
    """Simple chat history utilities for the local web service."""

    @staticmethod
    def exclude_spawn_events(events: list[dict]) -> list[dict]:
        """Filter out events from spawned sub-agents."""
        result = []
        for ev in events:
            source = str(ev.get('source') or '').lower()
            if any(source.startswith(p.lower()) for p in _SPAWN_PREFIXES):
                continue
            result.append(ev)
        return result

    @staticmethod
    def events_to_dialog_messages(events: list[dict]) -> list[dict]:
        """Convert history events to a simple dialog message list.

        Produces a flat list of {role, content} dicts suitable for passing
        as prior_messages to the agent.
        """
        messages: list[dict] = []
        for ev in events:
            ev_type = ev.get('type', '')
            content = ev.get('content')
            if ev_type == 'assistant_state':
                # assistant_state carries an AssistantMessage dump
                if isinstance(content, dict):
                    text = content.get('content') or ''
                    if text:
                        messages.append({'role': 'assistant', 'content': text})
            elif ev_type in ('user', 'user_message'):
                if content:
                    messages.append({'role': 'user', 'content': str(content)})
        return messages

    @staticmethod
    def summarize_dialog_messages_for_log(messages: list[dict]) -> str:
        """Short summary string for logging."""
        if not messages:
            return '(empty)'
        counts: dict[str, int] = {}
        for m in messages:
            role = m.get('role', '?')
            counts[role] = counts.get(role, 0) + 1
        return ', '.join(f'{r}×{n}' for r, n in counts.items())
