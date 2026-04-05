"""matmaster/tools/builtin/base.py

BuiltinTool ABC — base class for all matmaster builtin tools.

Satisfies the Tool Protocol (name/description/json_schema/execute).
Construction injection: session/workdir passed at Exp assemble time.
Kernel sees only Tool Protocol interface.

execute() is async, delegates to sync _execute() via asyncio.to_thread.
Subclasses implement sync _execute() only.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane


class BuiltinTool(ABC):
    """BuiltinTool base — satisfies matmaster Tool Protocol.

    Subclasses:
    - Define name, description, json_schema as ClassVar
    - Implement _execute(arguments) -> str | ToolResult (sync)
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
    prompt_exposure: ClassVar[str] = "system_prompt"

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: str | Path | None = None,
    ) -> None:
        self._session = session
        self._workdir = Path(workdir) if workdir is not None else None
        self.logger = logging.getLogger(self.__class__.__name__)

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Tool Protocol entry point. Delegates to _execute via to_thread."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        """Context-aware execution. Subclasses override for cancel_token/runner_state."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        """Dynamic description. Default returns self.description."""
        return self.description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        """LLM prompt injection. Default returns None."""
        return None

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        """Semantic input validation. Return None to allow, ToolDecision(deny) to reject."""
        return None

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Subclass implementation. Sync. Raise on error, return str or ToolResult."""
        ...

    def _require_session(self) -> Any:
        """Guard: raise if session not injected."""
        if self._session is None:
            raise RuntimeError(f"{self.name} requires a session but none was injected")
        return self._session

    def _cancel_token_for_exec(self) -> CancellationToken | None:
        """Cancel signal for session.exec_bash (injected by ToolCatalog.inject_cancel_token)."""
        ct = getattr(self, "_cancel_token", None)
        if ct is not None:
            return ct
        if self._session is not None:
            return getattr(self._session, "_cancel_token", None)
        return None
