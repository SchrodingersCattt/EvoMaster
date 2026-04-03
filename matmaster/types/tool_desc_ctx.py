"""Context passed to tool description and prompt hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matmaster.types.topology import RuntimeTopology


@dataclass(frozen=True)
class ToolDescriptionContext:
    """Session-scoped context for dynamic tool descriptions and prompts."""

    session_kind: str
    workspace_root: str
    topology: RuntimeTopology
