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
        "Update a task or a specific sub-task.\n\n"
        "Usage:\n"
        "- With subtask_index: update that specific sub-task's status.\n"
        "- Without subtask_index: update the parent task's description or status.\n"
        "- Parent task status is auto-derived when updating sub-tasks."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to update.",
            },
            "subtask_index": {
                "type": "integer",
                "description": (
                    "0-based index of the sub-task to update. "
                    "If provided, updates that specific sub-task's status."
                ),
            },
            "description": {
                "type": "string",
                "description": "New description for the task (parent-level only).",
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "completed"],
                "description": "New status.",
            },
        },
        "required": ["task_id"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task_id = arguments["task_id"]

        if "subtask_index" in arguments:
            status = arguments.get("status", "in_progress")
            task = store.update_subtask(task_id, arguments["subtask_index"], status)
            if task is None:
                return f"Task or subtask not found: {task_id}[{arguments['subtask_index']}]"
            return json.dumps(task, ensure_ascii=False)

        fields: dict[str, Any] = {}
        if "description" in arguments:
            fields["description"] = arguments["description"]
        if "status" in arguments:
            fields["status"] = arguments["status"]
        task = store.update(task_id, **fields)
        if task is None:
            return f"Task not found: {task_id}"
        return json.dumps(task, ensure_ascii=False)
