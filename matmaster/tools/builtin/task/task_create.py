"""TaskCreateTool -- create a new task for tracking work progress."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore
from matmaster.types.tool_spec import ResourceClaim


class TaskCreateTool(BuiltinTool):
    """Create a new task and return its JSON representation."""

    name: ClassVar[str] = "task_create"
    description: ClassVar[str] = (
        "Create a task with sub-tasks for tracking work progress.\n\n"
        "When to use: Multi-step tasks, complex requests needing planning.\n"
        "When NOT to use: Simple single-step tasks completable immediately.\n"
        "Parent task status is always auto-derived from sub-task statuses."
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
                    "individual status tracking (open/in_progress/completed)."
                ),
            },
        },
        "required": ["description", "tasks"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="task-store", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.write"})

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task = store.create(arguments["description"], subtasks=arguments["tasks"])
        return json.dumps(task, ensure_ascii=False)
