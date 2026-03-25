"""TaskStore -- read/write .tasks.json in workspace directory.

Thread-safe via internal lock. File format:
{
  "tasks": {
    "<uuid>": {
      "id": "<uuid>",
      "description": "...",
      "status": "open|in_progress|completed",
      "created_at": "ISO8601",
      "updated_at": "ISO8601"
    }
  }
}
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TaskStore:
    """Read/write .tasks.json in workspace directory.

    Thread-safe via class-level lock protecting all read-modify-write operations.
    """

    _lock = threading.Lock()

    def __init__(self, workdir: Path) -> None:
        self._path = workdir / ".tasks.json"

    def _read(self) -> dict[str, Any]:
        """Read tasks from file. Returns empty structure if file missing."""
        if not self._path.exists():
            return {"tasks": {}}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        """Write tasks to file with pretty formatting."""
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def create(self, description: str) -> dict[str, Any]:
        """Create a new task with open status. Returns the task dict."""
        with self._lock:
            data = self._read()
            task_id = str(uuid.uuid4())[:8]
            now = datetime.now(timezone.utc).isoformat()
            task = {
                "id": task_id,
                "description": description,
                "status": "open",
                "created_at": now,
                "updated_at": now,
            }
            data["tasks"][task_id] = task
            self._write(data)
            return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID. Returns None if not found."""
        with self._lock:
            data = self._read()
            return data["tasks"].get(task_id)

    def list_all(self) -> list[dict[str, Any]]:
        """List all tasks. Returns empty list if none exist."""
        with self._lock:
            data = self._read()
            return list(data["tasks"].values())

    def update(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update task fields (description, status). Returns None if not found."""
        with self._lock:
            data = self._read()
            task = data["tasks"].get(task_id)
            if task is None:
                return None
            for k, v in fields.items():
                if k in ("description", "status"):
                    task[k] = v
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write(data)
            return task

    def complete(self, task_id: str) -> dict[str, Any] | None:
        """Mark a task as completed. Returns None if not found."""
        return self.update(task_id, status="completed")
