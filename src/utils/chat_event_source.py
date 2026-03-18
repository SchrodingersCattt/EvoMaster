"""Helpers for normalizing chat event sources."""

from typing import Any

USER_SOURCE = 'User'
SYSTEM_SOURCE = 'System'
MATMASTER_SOURCE = 'MatMaster'


def normalize_event_source(source: Any) -> str:
    """Collapse event sources into the stable public set."""
    raw = str(source or '').strip()
    if raw == USER_SOURCE:
        return USER_SOURCE
    if raw == SYSTEM_SOURCE:
        return SYSTEM_SOURCE
    return MATMASTER_SOURCE
