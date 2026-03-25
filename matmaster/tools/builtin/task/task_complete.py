"""TaskCompleteTool -- mark a task as completed."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskCompleteTool(BuiltinTool):
    """Mark a task as completed."""

    name: ClassVar[str] = "task_complete"
    description: ClassVar[str] = "Mark a task as completed when it is done."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to mark as completed.",
            },
        },
        "required": ["task_id"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task_id = arguments["task_id"]
        task = store.complete(task_id)
        if task is None:
            return f"Task not found: {task_id}"
        return json.dumps(task, ensure_ascii=False)
