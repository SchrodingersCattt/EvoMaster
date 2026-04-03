"""TaskUpdateTool -- update a task's description or status."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore
from matmaster.types.tool_spec import ResourceClaim


class TaskUpdateTool(BuiltinTool):
    """Update a task's description or status."""

    name: ClassVar[str] = "task_update"
    description: ClassVar[str] = (
        "Update a specific sub-task's status.\n\n"
        "Usage:\n"
        "- Updates the sub-task at the given index.\n"
        "- Parent task status is auto-derived from sub-task statuses."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task.",
            },
            "subtask_index": {
                "type": "integer",
                "description": "0-based index of the sub-task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "completed"],
                "description": "New status for the sub-task.",
            },
        },
        "required": ["task_id", "subtask_index", "status"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="task-store", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.write"})

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task = store.update_subtask(
            arguments["task_id"],
            arguments["subtask_index"],
            arguments["status"],
        )
        if task is None:
            return (
                f"Task or subtask not found: "
                f"{arguments['task_id']}[{arguments['subtask_index']}]"
            )
        return json.dumps(task, ensure_ascii=False)
