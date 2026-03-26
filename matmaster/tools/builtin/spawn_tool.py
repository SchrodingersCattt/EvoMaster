"""SpawnTool -- spawn a child agent to execute a specific task.

The parent agent uses this tool to delegate tasks to specialized sub-agents.
Each sub-agent type is defined by an exp TOML (e.g. explore.toml) with its
own tool set and system prompt.

Recursion protection: spawn_fn=None prevents child agents from spawning
further children (schema-layer guard in TOML + runtime guard here).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool


class SpawnTool(BuiltinTool):
    """Spawn a sub-agent to execute a specific task.

    Overview: Delegates a task to a specialized sub-agent that runs
    independently with its own tool set and system prompt.

    Usage:
    - Specify exp_name to select the sub-agent type (e.g. 'explore')
    - Provide a complete task description with all necessary context
    - The sub-agent has no access to your conversation history
    - Results are returned as a text summary
    """

    name: ClassVar[str] = "spawn"
    description: ClassVar[str] = (
        "Spawn a sub-agent to execute a specific task. "
        "Use exp_name to select the sub-agent type (e.g. 'explore' for "
        "read-only code exploration). Provide a complete task description "
        "with all necessary context -- the sub-agent has no access to your "
        "conversation history. Results are returned as text."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "exp_name": {
                "type": "string",
                "description": (
                    "Name of the sub-agent type to spawn (e.g. 'explore'). "
                    "Must match an exp definition in matmaster/exps/."
                ),
            },
            "task": {
                "type": "string",
                "description": (
                    "Task description for the sub-agent to execute. "
                    "Include all necessary context -- the sub-agent has "
                    "no access to your conversation history."
                ),
            },
        },
        "required": ["exp_name", "task"],
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        spawn_fn: Callable[..., str] | None = None,
        available_exps: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._spawn_fn = spawn_fn
        self._stop_event: threading.Event | None = None

        # Override instance-level description/json_schema with available exps
        if available_exps:
            self._apply_available_exps(available_exps)

    def _apply_available_exps(self, exps: list[tuple[str, str]]) -> None:
        """Build instance-level description and json_schema from available exps.

        Shadows the ClassVar so the ToolRegistry sees the dynamic version.
        Adds enum constraint and per-exp descriptions to guide LLM selection.
        """
        names = [name for name, _ in exps]
        lines = [f"  - {name}: {desc}" for name, desc in exps if desc]
        if not lines:
            lines = [f"  - {name}" for name in names]

        exp_list_str = "\n".join(lines)
        # Instance attribute shadows ClassVar
        self.description = (  # type: ignore[misc]
            "Spawn a sub-agent to execute a specific task. "
            "Provide a complete task description with all necessary context "
            "-- the sub-agent has no access to your conversation history. "
            "Results are returned as text.\n\n"
            f"Available sub-agent types:\n{exp_list_str}"
        )
        self.json_schema = {  # type: ignore[misc]
            "type": "object",
            "properties": {
                "exp_name": {
                    "type": "string",
                    "enum": names,
                    "description": (
                        "Name of the sub-agent type to spawn. "
                        "Available types:\n" + exp_list_str
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Task description for the sub-agent to execute. "
                        "Include all necessary context -- the sub-agent has "
                        "no access to your conversation history."
                    ),
                },
            },
            "required": ["exp_name", "task"],
        }

    def _execute(self, arguments: dict[str, Any]) -> str:
        """Execute sub-agent spawn.

        Returns spawn_fn result on success, error string on guard/validation failure.
        Exceptions from spawn_fn propagate to BuiltinTool.execute() wrapper.
        """
        if self._spawn_fn is None:
            return (
                "Error: Spawn is not available in this context "
                "(recursion depth limit reached)"
            )

        exp_name = arguments.get("exp_name", "").strip()
        task = arguments.get("task", "").strip()

        if not exp_name or not task:
            return "Error: Both exp_name and task are required"

        return self._spawn_fn(exp_name, task, self._stop_event)
