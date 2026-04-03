"""Agent tool -- CC-style sub-agent launcher.

Differences from matmaster spawn:
- subagent_type: select predefined agent type (general-purpose, Explore, Plan)
- model: override model for the sub-agent (sonnet/opus/haiku)
- run_in_background: async background execution with notification
- isolation: 'worktree' for git worktree isolation
- description: short task summary for display
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Awaitable, ClassVar

from .base import BuiltinTool, ToolResult

# Available agent types and their tool restrictions
AGENT_TYPES: dict[str, dict[str, Any]] = {
    "general-purpose": {
        "description": "General-purpose agent for complex, multi-step tasks",
        "tool_filter": None,  # all tools
    },
    "Explore": {
        "description": "Fast agent for codebase exploration (read-only)",
        "tool_filter": lambda name: name not in ("Edit", "Write", "Agent"),
    },
    "Plan": {
        "description": "Software architect agent for designing plans (read-only)",
        "tool_filter": lambda name: name not in ("Edit", "Write", "Agent"),
    },
}

# Background task store
_agent_tasks: dict[str, asyncio.Task[Any]] = {}


class AgentTool(BuiltinTool):
    """Launch sub-agents for autonomous multi-step tasks."""

    name: ClassVar[str] = "Agent"
    description: ClassVar[str] = (
        "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
        "Available agent types:\n"
        "- general-purpose: Full capabilities for any task (default)\n"
        "- Explore: Fast read-only agent for codebase exploration\n"
        "- Plan: Software architect for designing implementation plans\n\n"
        "Usage notes:\n"
        "- Include a short description (3-5 words) of the task\n"
        "- Launch multiple agents concurrently for independent tasks\n"
        "- Use run_in_background=true for long-running tasks\n"
        "- Use isolation='worktree' for isolated git worktree execution\n"
        "- Agent results are not visible to user; summarize them in your response"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform",
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent type to use for this task",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Optional model override for this agent",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run agent in background; you will be notified on completion",
            },
            "isolation": {
                "type": "string",
                "enum": ["worktree"],
                "description": 'Isolation mode. "worktree" creates a temporary git worktree.',
            },
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        spawn_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._spawn_fn = spawn_fn

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Override to handle async spawning and background execution."""
        description = arguments.get("description", "sub-agent task")
        prompt = arguments.get("prompt", "")
        subagent_type = arguments.get("subagent_type", "general-purpose")
        model = arguments.get("model")
        run_bg = arguments.get("run_in_background", False)
        isolation = arguments.get("isolation")

        if not prompt:
            return "Error: prompt is required"

        # Validate agent type
        if subagent_type not in AGENT_TYPES:
            available = ", ".join(AGENT_TYPES.keys())
            return f"Error: unknown agent type '{subagent_type}'. Available: {available}"

        agent_config = AGENT_TYPES[subagent_type]

        if self._spawn_fn is None:
            return ToolResult.ok(
                f"Agent '{description}' would be spawned with:\n"
                f"  type: {subagent_type}\n"
                f"  model: {model or 'default'}\n"
                f"  isolation: {isolation or 'none'}\n"
                f"  background: {run_bg}\n"
                f"  prompt: {prompt[:200]}...",
                status="dry_run",
                subagent_type=subagent_type,
            )

        async def _run_agent() -> Any:
            return await self._spawn_fn(
                prompt=prompt,
                agent_type=subagent_type,
                model=model,
                isolation=isolation,
                tool_filter=agent_config.get("tool_filter"),
            )

        if run_bg:
            task_id = str(uuid.uuid4())[:8]
            task = asyncio.create_task(_run_agent())
            _agent_tasks[task_id] = task
            return ToolResult.ok(
                f"Agent '{description}' started in background",
                task_id=task_id,
                subagent_type=subagent_type,
            )

        try:
            result = await _run_agent()
            if isinstance(result, str):
                return result
            return ToolResult.ok(
                str(result),
                subagent_type=subagent_type,
                description=description,
            )
        except Exception as e:
            return f"Error: agent '{description}' failed: {e}"

    def _execute(self, arguments: dict[str, Any]) -> str:
        """Sync fallback -- not normally used."""
        return "Error: Agent tool requires async execution"

    @classmethod
    def get_agent_task(cls, task_id: str) -> asyncio.Task[Any] | None:
        return _agent_tasks.get(task_id)

    @classmethod
    def pop_agent_task(cls, task_id: str) -> asyncio.Task[Any] | None:
        return _agent_tasks.pop(task_id, None)
