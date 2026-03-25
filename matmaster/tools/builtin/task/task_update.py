"""TaskUpdateTool -- update a task's description or status."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskUpdateTool(BuiltinTool):
    """Update a task's description or status."""

    name: ClassVar[str] = "task_update"
    description: ClassVar[str] = (
        "Update a task's description or status. "
        "Status can be 'open', 'in_progress', or 'completed'."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to update.",
            },
            "description": {
                "type": "string",
                "description": "New description for the task.",
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "completed"],
                "description": "New status for the task.",
            },
        },
        "required": ["task_id"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task_id = arguments["task_id"]
        fields: dict[str, Any] = {}
        if "description" in arguments:
            fields["description"] = arguments["description"]
        if "status" in arguments:
            fields["status"] = arguments["status"]
        task = store.update(task_id, **fields)
        if task is None:
            return f"Task not found: {task_id}"
        return json.dumps(task, ensure_ascii=False)
