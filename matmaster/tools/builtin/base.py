"""BuiltinTool ABC -- base class for all matmaster native tools.

Satisfies the Tool Protocol (name/description/json_schema/execute).
Construction injection: session/workdir passed at Exp assemble time.
Kernel sees only Tool Protocol interface.

Subclasses:
- Define name, description, json_schema as ClassVar
- Implement _execute(arguments) -> str
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


class BuiltinTool(ABC):
    """BuiltinTool base -- satisfies matmaster Tool Protocol.

    Construction injection: session/workdir passed at Exp assemble time.
    Kernel sees only Tool Protocol (name/description/json_schema/execute).

    Subclasses:
    - Define name, description, json_schema as class-level attributes
    - Implement _execute(arguments) -> str
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

    def execute(self, arguments: dict[str, Any]) -> str:
        """Tool Protocol entry point. Delegates to _execute."""
        try:
            return self._execute(arguments)
        except Exception as e:
            self.logger.error('Tool %s failed: %s', self.name, e, exc_info=True)
            return f'Error: {e}'

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str:
        """Subclass implementation. Raise on error, return string on success."""
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
