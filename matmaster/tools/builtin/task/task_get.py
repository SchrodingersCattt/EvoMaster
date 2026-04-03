"""TaskGetTool -- retrieve a task by ID."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore
from matmaster.types.tool_spec import ResourceClaim


class TaskGetTool(BuiltinTool):
    """Get a task by its ID and return its JSON representation."""

    name: ClassVar[str] = "task_get"
    description: ClassVar[str] = (
        "Get a task by its ID to check current status and details.\n\n"
        "Usage:\n"
        "- Use after task_create to verify task was created correctly.\n"
        "- Use to check task status before updating or completing."
    )
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
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="task-store", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.read"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task_id = arguments["task_id"]
        task = store.get(task_id)
        if task is None:
            return f"Task not found: {task_id}"
        return json.dumps(task, ensure_ascii=False)
