"""Guard Protocol and supporting types for tool call evaluation.

Guard is the interface for evaluating whether a tool call should be allowed.
GuardContext is constructed by the kernel before each tool call.
GuardResult contains the decision (allow/deny with optional reason and guidance).
RecentCall records a single tool invocation for the sliding window in GuardContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class RecentCall:
    """Single tool call record for GuardContext.recent_calls sliding window.

    Maintained by the kernel and injected into GuardContext before
    each guard evaluation.
    """

    tool_name: str
    tool_args: dict[str, Any]
    call_id: str
    timestamp: float  # time.monotonic()
    fingerprint: str = ""


@dataclass
class GuardContext:
    """Guard evaluation context, constructed by the kernel before each tool call.

    Contains the current tool call details and recent history for
    pattern-based guards (e.g. loop detection).
    """

    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    current_turn: int
    max_turns: int
    recent_calls: list[RecentCall] = field(default_factory=list)
    read_tracker: Any | None = None  # Actual type: ReadTracker (avoid circular import)


@dataclass
class GuardResult:
    """Result of a guard evaluation.

    allowed=True means the tool call proceeds.
    allowed=False blocks the call; reason explains why, guidance is
    injected into the LLM prompt to steer away from the blocked pattern.
    """

    allowed: bool
    reason: str | None = None
    guidance: str | None = None


@runtime_checkable
class Guard(Protocol):
    """Guard interface: evaluate whether a tool call should be allowed.

    Implementations may be stateful (e.g. maintain a deque of recent calls
    for loop detection). The Protocol only constrains the interface, not
    internal state management.
    """

    def evaluate(self, ctx: GuardContext) -> GuardResult: ...
