"""TaskCreateTool -- create a new task for tracking work progress."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskCreateTool(BuiltinTool):
    """Create a new task and return its JSON representation."""

    name: ClassVar[str] = "task_create"
    description: ClassVar[str] = (
        "Create a task for tracking work progress.\n\n"
        "When to use: Multi-step tasks, complex requests needing planning.\n"
        "When NOT to use: Simple single-step tasks completable immediately."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Description of the task to create.",
            },
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of sub-task descriptions. Each sub-task gets "
                    "individual status tracking (open/in_progress/completed). "
                    "Parent task status is auto-derived from sub-tasks."
                ),
            },
        },
        "required": ["description"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task = store.create(
            arguments["description"],
            subtasks=arguments.get("tasks"),
        )
        return json.dumps(task, ensure_ascii=False)
