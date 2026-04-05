"""Generic multi-source credential resolver.

Implements the precedence chain: explicit > session > env > none.
This module is service-agnostic -- service-specific extraction logic
lives in individual adapters.
"""

from __future__ import annotations

import os
from typing import Any

from matmaster.integration.runtime_bridge.models import ResolvedCredential


def resolve_credentials(
    service: str,
    *,
    explicit: dict[str, Any] | None,
    session_extractor: Any | None,
    env_keys: dict[str, str],
    required_keys: list[str],
) -> ResolvedCredential:
    """Resolve credentials through the precedence chain.

    Args:
        service: Service identifier (e.g. ``"bohrium"``).
        explicit: Caller-provided credential dict (highest priority).
        session_extractor: A callable ``() -> dict | None`` that extracts
            credentials from the current session.
        env_keys: Mapping from credential field name to env var name,
            used for os.environ fallback.
        required_keys: Field names that must be non-empty for the result
            to be considered valid.

    Returns:
        A ``ResolvedCredential`` with source reflecting which layer
        provided the values.
    """
    # 1. Explicit params
    if explicit:
        merged = _merge_explicit(explicit, env_keys)
        if _has_required(merged, required_keys):
            return ResolvedCredential(
                service=service, source="explicit", values=merged
            )

    # 2. Session credentials
    if session_extractor is not None:
        session_values = session_extractor()
        if session_values:
            merged = _merge_session(session_values, env_keys)
            if _has_required(merged, required_keys):
                return ResolvedCredential(
                    service=service, source="session", values=merged
                )

    # 3. Environment variables
    env_values = _read_env(env_keys)
    if _has_required(env_values, required_keys):
        return ResolvedCredential(service=service, source="env", values=env_values)

    # 4. Nothing found
    return ResolvedCredential(service=service, source="none", values={})


def _merge_explicit(
    explicit: dict[str, Any], env_keys: dict[str, str]
) -> dict[str, Any]:
    """Start from explicit values, fill missing keys from env."""
    result = dict(explicit)
    for field, env_var in env_keys.items():
        if field not in result or result[field] is None:
            val = os.environ.get(env_var)
            if val is not None:
                result[field] = val
    return result


def _merge_session(
    session_values: dict[str, Any], env_keys: dict[str, str]
) -> dict[str, Any]:
    """Start from session values, fill missing keys from env."""
    result = dict(session_values)
    for field, env_var in env_keys.items():
        if field not in result or result[field] is None:
            val = os.environ.get(env_var)
            if val is not None:
                result[field] = val
    return result


def _read_env(env_keys: dict[str, str]) -> dict[str, Any]:
    """Read all known env vars for a service."""
    result: dict[str, Any] = {}
    for field, env_var in env_keys.items():
        val = os.environ.get(env_var)
        if val is not None:
            result[field] = val
    return result


def _has_required(values: dict[str, Any], required_keys: list[str]) -> bool:
    """Check that all required keys are present and non-empty."""
    for key in required_keys:
        val = values.get(key)
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
    return True
