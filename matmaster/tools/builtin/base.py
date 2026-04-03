"""BuiltinTool ABC -- base class for all matmaster native tools.

Satisfies the Tool Protocol (name/description/json_schema/execute).
Construction injection: session/workdir passed at Exp assemble time.
Kernel sees only Tool Protocol interface.

execute() is async def, delegates to sync _execute() via asyncio.to_thread.
Subclasses implement sync _execute() only -- no async needed in subclass code.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane


class BuiltinTool(ABC):
    """BuiltinTool base -- satisfies matmaster Tool Protocol.

    Construction injection: session/workdir passed at Exp assemble time.
    Kernel sees only Tool Protocol (name/description/json_schema/execute).

    execute() is async def and delegates to sync _execute() via asyncio.to_thread,
    ensuring blocking tool operations do not stall the event loop.

    Subclasses:
    - Define name, description, json_schema as class-level attributes
    - Implement _execute(arguments) -> str | ToolResult (sync def)
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    json_schema: ClassVar[dict[str, Any]]
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset()
    effect_level: ClassVar[str] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    max_result_chars: ClassVar[int] = 0
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE
    state_mode: ClassVar[str] = "stateless"
    stop_mode: ClassVar[str] = "cancellable"
    exposed_to_model: ClassVar[bool] = True

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
        """Tool Protocol entry point. Delegates to _execute via to_thread."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error('Tool %s failed: %s', self.name, e, exc_info=True)
            return f'Error: {e}'

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        return self.description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return None

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error('Tool %s failed: %s', self.name, e, exc_info=True)
            return f'Error: {e}'

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        """Tool-specific semantic input validation.

        Override to reject invalid arguments before execution.
        Return None to allow, ToolDecision(decision='deny', ...) to reject.
        """
        return None

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Subclass implementation. Raise on error, return string or ToolResult on success."""
        ...

    def _require_session(self) -> Any:
        """Guard: raise if session not injected (session-dependent tools)."""
        if self._session is None:
            raise RuntimeError(f'{self.name} requires a session but none was injected')
        return self._session

    def _stop_event_for_exec(self) -> Any:
        """Cancel signal for session.exec_bash (injected on tool by Exp / AgentRunService)."""
        ev = getattr(self, '_stop_event', None)
        if ev is not None:
            return ev
        if self._session is not None:
            return getattr(self._session, '_stop_event', None)
        return None
