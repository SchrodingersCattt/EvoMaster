"""TodoWrite tool -- CC-style atomic todo list replacement.

Key difference from matmaster task_create/list/get/update/complete:
- Single tool with atomic replacement semantics (replaces entire list each call)
- Three states: pending / in_progress / completed
- Each item has content (imperative) and activeForm (present continuous)
"""

from __future__ import annotations

from typing import Any, ClassVar

from .base import BuiltinTool, ToolResult


class TodoItem:
    """Single todo item."""

    __slots__ = ("content", "status", "active_form")

    def __init__(self, content: str, status: str, active_form: str) -> None:
        self.content = content
        self.status = status
        self.active_form = active_form

    def to_dict(self) -> dict[str, str]:
        return {
            "content": self.content,
            "status": self.status,
            "activeForm": self.active_form,
        }

    def __repr__(self) -> str:
        icon = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        return f"{icon.get(self.status, '[?]')} {self.content}"


# Per-agent todo storage: agent_id -> list of items
_todo_store: dict[str, list[TodoItem]] = {}


class TodoWriteTool(BuiltinTool):
    """Atomic todo list management for tracking multi-step task progress."""

    name: ClassVar[str] = "TodoWrite"
    description: ClassVar[str] = (
        "Create and manage a structured task list for the current session.\n\n"
        "When to use:\n"
        "- Complex multi-step tasks (3+ steps)\n"
        "- User provides multiple tasks\n"
        "- After receiving new instructions\n\n"
        "When NOT to use:\n"
        "- Single, trivial tasks\n"
        "- Purely conversational requests\n\n"
        "Task states:\n"
        "- pending: not yet started\n"
        "- in_progress: currently working on (limit to ONE at a time)\n"
        "- completed: finished successfully\n\n"
        "Each item needs:\n"
        '- content: imperative form (e.g., "Run tests")\n'
        '- activeForm: present continuous (e.g., "Running tests")\n\n'
        "The entire todo list is replaced atomically on each call."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list (replaces previous list entirely)",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "description": 'Imperative task description (e.g., "Fix auth bug")',
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {
                            "type": "string",
                            "minLength": 1,
                            "description": 'Present continuous form (e.g., "Fixing auth bug")',
                        },
                    },
                    "required": ["content", "status", "activeForm"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        agent_id: str = "main",
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._agent_id = agent_id

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        raw_todos: list[dict[str, Any]] = arguments.get("todos", [])

        # Validate items
        new_items: list[TodoItem] = []
        for item in raw_todos:
            content = item.get("content", "").strip()
            status = item.get("status", "pending")
            active_form = item.get("activeForm", "").strip()

            if not content:
                return "Error: each todo must have non-empty content"
            if status not in ("pending", "in_progress", "completed"):
                return f"Error: invalid status '{status}'"
            if not active_form:
                return "Error: each todo must have non-empty activeForm"

            new_items.append(TodoItem(content, status, active_form))

        # Get old state for comparison
        old_items = _todo_store.get(self._agent_id, [])

        # Atomic replacement
        if new_items:
            _todo_store[self._agent_id] = new_items
        else:
            _todo_store.pop(self._agent_id, None)

        # Build summary
        counts = {"pending": 0, "in_progress": 0, "completed": 0}
        for item in new_items:
            counts[item.status] += 1

        return ToolResult.ok(
            f"Todos updated: {counts['completed']} done, "
            f"{counts['in_progress']} in progress, "
            f"{counts['pending']} pending",
            old_count=len(old_items),
            new_count=len(new_items),
            counts=counts,
        )

    @classmethod
    def get_todos(cls, agent_id: str = "main") -> list[TodoItem]:
        """Retrieve current todo list for an agent."""
        return _todo_store.get(agent_id, [])

    @classmethod
    def clear_todos(cls, agent_id: str = "main") -> None:
        """Clear todo list for an agent."""
        _todo_store.pop(agent_id, None)
