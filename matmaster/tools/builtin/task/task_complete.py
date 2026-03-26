"""TaskCompleteTool -- mark a task as completed."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskCompleteTool(BuiltinTool):
    """Mark a task as completed."""

    name: ClassVar[str] = "task_complete"
    description: ClassVar[str] = (
        "Mark a task or a specific sub-task as completed.\n\n"
        "Usage:\n"
        "- With subtask_index: complete that specific sub-task.\n"
        "- Without subtask_index: complete the entire task (all sub-tasks).\n"
        "- Parent task status is auto-derived from sub-task statuses."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to mark as completed.",
            },
            "subtask_index": {
                "type": "integer",
                "description": (
                    "0-based index of the sub-task to complete. "
                    "If omitted, completes the entire task (all sub-tasks)."
                ),
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
            task = store.complete_subtask(task_id, arguments["subtask_index"])
            if task is None:
                return f"Task or subtask not found: {task_id}[{arguments['subtask_index']}]"
        else:
            task = store.complete(task_id)
            if task is None:
                return f"Task not found: {task_id}"
        return json.dumps(task, ensure_ascii=False)
