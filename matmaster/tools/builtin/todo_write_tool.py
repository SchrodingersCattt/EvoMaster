"""TodoWriteTool -- session task tracking with full-replacement semantics."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim

VALID_STATUSES = {"pending", "in_progress", "completed"}
VALID_PRIORITIES = {"low", "medium", "high"}


class TodoWriteTool(BuiltinTool):
    """Update the todo list for the current session."""

    name: ClassVar[str] = "TodoWrite"
    description: ClassVar[str] = (
        "Update the todo list for the current session. To be used proactively "
        "and often to track progress and pending tasks."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique identifier"},
                        "content": {
                            "type": "string",
                            "description": "Task description",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Task status",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "Task priority (optional)",
                        },
                    },
                    "required": ["id", "content", "status"],
                },
                "description": "The updated todo list (full replacement)",
            }
        },
        "required": ["todos"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="todo-store", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.write"})
    effect_level: ClassVar[str] = "local_mutation"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()

    def prompt(self, ctx=None) -> str:
        return (
            "Use this tool to create and manage a structured task list for your "
            "current session.\n\n"
            "## When to Use\n"
            "- Complex multi-step tasks (3+ steps)\n"
            "- User provides multiple tasks\n"
            "- When starting work on a task\n"
            "- After completing a task\n\n"
            "## Task Management\n"
            "- Update status in real-time as you work\n"
            "- Mark tasks complete immediately after finishing\n"
            "- Only one task should be in_progress at a time"
        )

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        if self._workdir is None:
            return ToolResult(status="error", content="Error: workdir not available")

        todos = arguments.get("todos", [])
        for todo in todos:
            if not isinstance(todo, dict):
                return ToolResult(
                    status="error",
                    content="Error: each todo must be an object",
                )
            for field in ("id", "content", "status"):
                if field not in todo:
                    return ToolResult(
                        status="error",
                        content=f"Error: todo missing required field '{field}'",
                    )
            if todo["status"] not in VALID_STATUSES:
                return ToolResult(
                    status="error",
                    content=(
                        f"Error: invalid status '{todo['status']}'. "
                        f"Must be one of: {sorted(VALID_STATUSES)}"
                    ),
                )
            if "priority" in todo and todo["priority"] not in VALID_PRIORITIES:
                return ToolResult(
                    status="error",
                    content=(
                        f"Error: invalid priority '{todo['priority']}'. "
                        f"Must be one of: {sorted(VALID_PRIORITIES)}"
                    ),
                )

        path = Path(self._workdir) / ".todos.json"
        with self._lock:
            old_todos: list[dict[str, Any]] = []
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    old_todos = data.get("todos", [])
                except Exception:
                    old_todos = []

            all_done = bool(todos) and all(
                todo["status"] == "completed" for todo in todos
            )
            new_todos = [] if all_done else todos
            path.write_text(
                json.dumps({"todos": new_todos}, indent=2),
                encoding="utf-8",
            )

        old_ids = {todo.get("id") for todo in old_todos}
        new_ids = {todo.get("id") for todo in todos}
        added = len(new_ids - old_ids)
        removed = len(old_ids - new_ids)
        updated = len(old_ids & new_ids)
        completed = sum(1 for todo in todos if todo["status"] == "completed")

        summary = (
            f"Todos updated: {added} added, {updated} updated, "
            f"{removed} removed, {completed} completed"
        )
        if all_done:
            summary += " (all done, list cleared)"

        return ToolResult(status="success", content=summary)
