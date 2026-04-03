"""TaskCompleteTool -- mark a task as completed."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore
from matmaster.types.tool_spec import ResourceClaim


class TaskCompleteTool(BuiltinTool):
    """Mark a task as completed."""

    name: ClassVar[str] = "task_complete"
    description: ClassVar[str] = (
        "Mark a specific sub-task as completed.\n\n"
        "Usage:\n"
        "- Completes the sub-task at the given index.\n"
        "- Parent task status is auto-derived from sub-task statuses.\n"
        "- When all sub-tasks are completed, the parent task is also completed."
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
                "description": "0-based index of the sub-task to complete.",
            },
        },
        "required": ["task_id", "subtask_index"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="task-store", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.write"})

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task = store.complete_subtask(
            arguments["task_id"],
            arguments["subtask_index"],
        )
        if task is None:
            return (
                f"Task or subtask not found: "
                f"{arguments['task_id']}[{arguments['subtask_index']}]"
            )
        return json.dumps(task, ensure_ascii=False)
