"""CC-Tools base class -- mirrors matmaster BuiltinTool pattern.

Self-contained base so cc-tools can be used independently of matmaster internals.
Same Protocol contract: name / description / json_schema / execute.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# ToolResult (local copy of matmaster.tools.tool_result)
# ---------------------------------------------------------------------------


class ToolResult(BaseModel):
    """Structured tool execution result."""

    model_config = ConfigDict(extra="forbid")

    status: str = "success"
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(cls, content: str = "", **payload: Any) -> ToolResult:
        return cls(status="success", content=content, payload=payload)

    @classmethod
    def error(cls, content: str) -> ToolResult:
        return cls(status="error", content=content)


def normalize_tool_result(raw: str | ToolResult | None) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    if raw is None:
        return ToolResult()
    content = str(raw)
    status = "error" if content.lstrip().startswith("Error:") else "success"
    return ToolResult(status=status, content=content)


# ---------------------------------------------------------------------------
# BuiltinTool ABC
# ---------------------------------------------------------------------------


class BuiltinTool(ABC):
    """Base class for CC-style builtin tools.

    Subclasses define:
      - name, description, json_schema as ClassVar
      - _execute(arguments) -> str | ToolResult  (sync)

    execute() delegates to _execute via asyncio.to_thread.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    json_schema: ClassVar[dict[str, Any]]

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Path | None = None,
    ) -> None:
        self._session = session
        self._workdir = workdir
        self.logger = logging.getLogger(self.__class__.__name__)

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        ...

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError(f"{self.name} requires a session but none was injected")
        return self._session
