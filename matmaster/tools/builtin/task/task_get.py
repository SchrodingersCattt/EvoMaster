"""TaskGetTool -- retrieve a task by ID."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskGetTool(BuiltinTool):
    """Get a task by its ID and return its JSON representation."""

    name: ClassVar[str] = "task_get"
    description: ClassVar[str] = "Get a task by its ID to check status and details."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to retrieve.",
            },
        },
        "required": ["task_id"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task_id = arguments["task_id"]
        task = store.get(task_id)
        if task is None:
            return f"Task not found: {task_id}"
        return json.dumps(task, ensure_ascii=False)
