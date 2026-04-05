"""Data models for the runtime credential bridge.

Immutable value objects representing resolved credentials and output path
decisions. These are the return types of the bridge public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ResolvedCredential:
    """Result of multi-source credential resolution.

    Attributes:
        service: Service name (e.g. ``"bohrium"``).
        source: Which layer provided the credentials.
        values: Resolved credential key-value pairs.
    """

    service: str
    source: Literal["explicit", "session", "env", "none"]
    values: dict[str, Any]


@dataclass(frozen=True)
class OutputPathDecision:
    """Classification of an output path for local vs remote handling.

    Attributes:
        kind: Path type classification.
        normalized_path: Absolute resolved path.
        requires_remote_session: Whether a remote session is needed to
            access this path.
    """

    kind: Literal["relative", "local_abs", "remote_share"]
    normalized_path: str
    requires_remote_session: bool
