"""Runner-level mutable state shared across tool executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolRunnerState:
    """Shared state store owned by a tool runner instance.

    THREAD SAFETY CONTRACT:
    - runner_state MUST only be accessed in the asyncio event loop thread,
      i.e., AFTER ``await asyncio.to_thread()`` returns.
    - NEVER access runner_state inside sync ``_execute()`` methods or in
      any code running in the thread pool.
    - asyncio is cooperative single-threaded concurrency: between await
      points, no other coroutine runs, so dict reads/writes are atomic.
    """

    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def clear(self) -> None:
        self.data.clear()
