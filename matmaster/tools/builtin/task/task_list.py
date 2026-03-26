"""TaskListTool -- list all tasks in the workspace."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.task._store import TaskStore


class TaskListTool(BuiltinTool):
    """List all tasks and return them as a JSON array."""

    name: ClassVar[str] = "task_list"
    description: ClassVar[str] = (
        "List all tasks to see current work tracking status.\n\n"
        "Usage:\n"
        "- Use at the start of a session to see pending work.\n"
        "- Returns all tasks with their IDs, descriptions, and statuses."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        tasks = store.list_all()
        return json.dumps(tasks, ensure_ascii=False)
