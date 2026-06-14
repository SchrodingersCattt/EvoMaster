"""Shared run-stream drain result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from matmaster.types.events import FinishDetail


@dataclass
class DrainResult:
    """Structured terminal result from draining a run_stream() to completion."""

    status: str
    reason: str
    final_content: str | None
    num_turns: int
    usage: dict[str, int]
    messages: list[Any]
    usage_vendor_by_turn: tuple[dict[str, Any], ...] = ()
    finish_detail: FinishDetail | None = None
    events: list[Any] = field(default_factory=list)
    spawn_id: str | None = None
