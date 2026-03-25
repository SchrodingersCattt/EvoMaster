"""TaskCreateTool -- create a new task for tracking work progress."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskCreateTool(BuiltinTool):
    """Create a new task and return its JSON representation."""

    name: ClassVar[str] = "task_create"
    description: ClassVar[str] = "Create a new task for tracking work progress."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Description of the task to create.",
            },
        },
        "required": ["description"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task = store.create(arguments["description"])
        return json.dumps(task, ensure_ascii=False)
