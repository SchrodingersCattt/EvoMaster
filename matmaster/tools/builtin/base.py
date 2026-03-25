"""BuiltinTool ABC -- base class for matmaster native builtin tools.

Provides a template-method execute() that delegates to _execute(),
with automatic error handling and logging. Subclasses implement _execute()
and declare name/description/json_schema as ClassVar.

Satisfies the matmaster Tool Protocol (tool_registry.Tool) so that
builtin tools can be registered in ToolRegistry without an adapter.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


class BuiltinTool(ABC):
    """Abstract base class for matmaster native builtin tools.

    Satisfies the Tool Protocol: name, description, json_schema, execute.
    Subclasses declare these as ClassVar and implement _execute().

    Constructor accepts optional session and workdir for dependency injection.
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
        """Template method: delegate to _execute with error handling."""
        try:
            return self._execute(arguments)
        except Exception as e:
            self.logger.error(
                "%s execute failed: %s", self.name, e, exc_info=True
            )
            return f"Error: {e}"

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str:
        """Subclass implementation of tool logic."""
        ...

    def _require_session(self) -> Any:
        """Guard: raise RuntimeError if session was not injected."""
        if self._session is None:
            raise RuntimeError(
                f"{self.name} requires a session but none was injected"
            )
        return self._session
