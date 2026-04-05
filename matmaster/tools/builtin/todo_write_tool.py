"""TodoWriteTool -- Claude Code-style session task tracking."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from textwrap import dedent
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ResourceClaim

VALID_STATUSES = {"pending", "in_progress", "completed"}
TODO_FIELDS = ("content", "status", "activeForm")

DESCRIPTION = (
    "Update the todo list for the current session. To be used proactively "
    "and often to track progress and pending tasks. Make sure that at least "
    "one task is in_progress at all times. Always provide both content "
    "(imperative) and activeForm (present continuous) for each task."
)

PROMPT = dedent(
    """
    Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
    It also helps the user understand the progress of the task and overall progress of their requests.

    ## When to Use This Tool
    Use this tool proactively in these scenarios:

    1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
    2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
    3. User explicitly requests todo list - When the user directly asks you to use the todo list
    4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
    5. After receiving new instructions - Immediately capture user requirements as todos
    6. When you start working on a task - Mark it as in_progress BEFORE beginning work. Ideally you should only have one todo as in_progress at a time
    7. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

    ## When NOT to Use This Tool

    Skip using this tool when:
    1. There is only a single, straightforward task
    2. The task is trivial and tracking it provides no organizational benefit
    3. The task can be completed in less than 3 trivial steps
    4. The task is purely conversational or informational

    NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

    ## Examples of When to Use the Todo List

    <example>
    User: I want to add a dark mode toggle to the application settings. Make sure you run the tests and build when you're done!
    Assistant: Creates todo list with the following items:
    1. Creating dark mode toggle component in Settings page
    2. Adding dark mode state management (context/store)
    3. Implementing CSS-in-JS styles for dark theme
    4. Updating existing components to support theme switching
    5. Running tests and build process, addressing any failures or errors that occur
    Begins working on the first task
    </example>

    <example>
    User: Help me rename the function getCwd to getCurrentWorkingDirectory across my project
    Assistant: Uses search tools to locate all instances of getCwd in the codebase, then creates todo items for each affected file.
    </example>

    <example>
    User: I need to implement these features for my e-commerce site: user registration, product catalog, shopping cart, and checkout flow.
    Assistant: Creates a todo list breaking down each feature into specific tasks based on the project architecture.
    </example>

    <example>
    User: Can you help optimize my React application? It's rendering slowly and has performance issues.
    Assistant: Reviews component structure and then creates todo items for each optimization opportunity.
    </example>

    ## Examples of When NOT to Use the Todo List

    <example>
    User: How do I print Hello World in Python?
    Assistant: Answers directly without using the todo list because this is a single trivial request.
    </example>

    <example>
    User: What does the git status command do?
    Assistant: Explains the command directly because this is informational only.
    </example>

    <example>
    User: Can you add a comment to the calculateTotal function to explain what it does?
    Assistant: Uses the Edit tool directly because this is a single straightforward code edit.
    </example>

    <example>
    User: Run npm install for me and tell me what happens.
    Assistant: Runs the command directly because there are no multiple steps to organize.
    </example>

    ## Task States and Management

    1. Task States: Use these states to track progress:
       - pending: Task not yet started
       - in_progress: Currently working on (limit to ONE task at a time)
       - completed: Task finished successfully

       IMPORTANT: Task descriptions must have two forms:
       - content: The imperative form describing what needs to be done (for example, Run tests or Build the project)
       - activeForm: The present continuous form shown during execution (for example, Running tests or Building the project)

    2. Task Management:
       - Update task status in real-time as you work
       - Mark tasks complete IMMEDIATELY after finishing (do not batch completions)
       - Exactly ONE task must be in_progress at any time (not less, not more)
       - Complete current tasks before starting new ones
       - Remove tasks that are no longer relevant from the list entirely

    3. Task Completion Requirements:
       - ONLY mark a task as completed when you have FULLY accomplished it
       - If you encounter errors, blockers, or cannot finish, keep the task as in_progress
       - When blocked, create a new task describing what needs to be resolved
       - Never mark a task as completed if:
         - Tests are failing
         - Implementation is partial
         - You encountered unresolved errors
         - You could not find necessary files or dependencies

    4. Task Breakdown:
       - Create specific, actionable items
       - Break complex tasks into smaller, manageable steps
       - Use clear, descriptive task names
       - Always provide both forms:
         - content: Fix authentication bug
         - activeForm: Fixing authentication bug

    When in doubt, use this tool. Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully.
    """
).strip()

SUCCESS_MESSAGE = (
    "Todo list updated successfully. Continue to use the todo list to track "
    "your progress. Proceed with the current tasks if applicable."
)


class TodoWriteTool(BuiltinTool):
    """Update the todo list for the current session."""

    name: ClassVar[str] = "TodoWrite"
    description: ClassVar[str] = DESCRIPTION
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Imperative task description",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Task status",
                        },
                        "activeForm": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Present continuous task description",
                        },
                    },
                    "required": ["content", "status", "activeForm"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["todos"],
        "additionalProperties": False,
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="todo-store", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.write"})
    effect_level: ClassVar[str] = "local_mutation"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()

    def prompt(self, ctx=None) -> str:
        return PROMPT

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state=None,
    ) -> ToolDecision | None:
        reason = self._validate_arguments(arguments)
        if reason is None:
            return None
        return ToolDecision(decision="deny", reason=reason)

    @staticmethod
    def _validate_arguments(arguments: dict[str, Any]) -> str | None:
        todos = arguments.get("todos")
        if not isinstance(todos, list):
            return "todos must be an array"

        normalized_todos: list[dict[str, str]] = []
        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                return f"todo at index {index} must be an object"

            for field in TODO_FIELDS:
                if field not in todo:
                    return f"todo missing required field '{field}'"
                if not isinstance(todo[field], str):
                    return f"todo field '{field}' must be a string"
                if field != "status" and not todo[field].strip():
                    return f"todo field '{field}' must not be empty"

            extra_fields = set(todo) - set(TODO_FIELDS)
            if extra_fields:
                names = ", ".join(sorted(extra_fields))
                return f"todo contains unsupported fields: {names}"

            status = todo["status"]
            if status not in VALID_STATUSES:
                return (
                    f"invalid status '{status}'. Must be one of: "
                    f"{sorted(VALID_STATUSES)}"
                )

            normalized_todos.append(
                {
                    "content": todo["content"].strip(),
                    "status": status,
                    "activeForm": todo["activeForm"].strip(),
                }
            )

        if normalized_todos and not all(
            todo["status"] == "completed" for todo in normalized_todos
        ):
            in_progress_count = sum(
                1 for todo in normalized_todos if todo["status"] == "in_progress"
            )
            if in_progress_count != 1:
                return (
                    "exactly one todo must be in_progress unless all todos are "
                    "completed"
                )

        return None

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        if self._workdir is None:
            return ToolResult(status="error", content="Error: workdir not available")

        reason = self._validate_arguments(arguments)
        if reason is not None:
            return ToolResult(status="error", content=f"Error: {reason}")

        todos = [
            {
                "content": todo["content"].strip(),
                "status": todo["status"],
                "activeForm": todo["activeForm"].strip(),
            }
            for todo in arguments.get("todos", [])
        ]

        path = Path(self._workdir) / ".todos.json"
        with self._lock:
            old_todos: list[dict[str, Any]] = []
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    loaded = data.get("todos", [])
                    if isinstance(loaded, list):
                        old_todos = loaded
                except Exception:
                    old_todos = []

            all_done = bool(todos) and all(
                todo["status"] == "completed" for todo in todos
            )
            new_todos = [] if all_done else todos
            path.write_text(
                json.dumps({"todos": new_todos}, indent=2),
                encoding="utf-8",
            )

        return ToolResult(
            status="success",
            content=SUCCESS_MESSAGE,
            payload={
                "oldTodos": old_todos,
                "newTodos": todos,
            },
        )
