"""Data models for the runtime credential bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ResolvedCredential:
    service: str
    source: Literal["explicit", "session", "env", "none"]
    values: dict[str, Any]
