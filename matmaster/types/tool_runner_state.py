"""Runner-level mutable state shared across tool executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolRunnerState:
    """Shared state store owned by a tool runner instance."""

    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def clear(self) -> None:
        self.data.clear()
