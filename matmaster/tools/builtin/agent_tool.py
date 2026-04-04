"""AgentTool -- spawn a sub-agent to execute a specific task."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_spec import ResourceClaim

SpawnFn = Callable[[str, str, CancellationToken | None], Awaitable[str]]


class AgentTool(BuiltinTool):
    """Spawn a sub-agent to execute a specific task."""

    name: ClassVar[str] = "Agent"
    description: ClassVar[str] = (
        "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
        "The Agent tool launches specialized agents that autonomously handle "
        "complex tasks. Each agent type has specific capabilities and tools."
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
            "exp_name": {
                "type": "string",
                "description": "The type of specialized agent to use for this task",
            },
        },
        "required": ["description", "prompt"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="spawn", mode="counted", max_concurrent=2),
    )
    stop_mode: ClassVar[str] = "non_cancellable"

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        spawn_fn: SpawnFn | None = None,
        available_exps: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._spawn_fn = spawn_fn
        self._valid_exp_names: frozenset[str] = frozenset()

        if spawn_fn is None:
            self.exposed_to_model = False

        if available_exps:
            self._valid_exp_names = frozenset(name for name, _ in available_exps)
            self._apply_available_exps(available_exps)

    def _apply_available_exps(self, exps: list[tuple[str, str]]) -> None:
        names = [name for name, _ in exps]
        lines = [f"  - {name}: {desc}" for name, desc in exps if desc]
        if not lines:
            lines = [f"  - {name}" for name in names]
        exp_list_str = "\n".join(lines)

        self.description = (
            "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
            "Provide a complete task description with all necessary context "
            "because the sub-agent has no access to your conversation history.\n\n"
            f"Available sub-agent types:\n{exp_list_str}"
        )
        self.json_schema = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A short (3-5 word) description of the task",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "The task for the agent to perform. "
                        "Include all necessary context."
                    ),
                },
                "exp_name": {
                    "type": "string",
                    "enum": names,
                    "description": f"Sub-agent type:\n{exp_list_str}",
                },
            },
            "required": ["description", "prompt"],
        }

    def prompt(self, ctx=None) -> str:
        return (
            "Usage notes:\n"
            "- Always include a short description (3-5 words) summarizing what "
            "the agent will do\n"
            "- When the agent is done, it will return a single message back to you. "
            "The result returned by the agent is not visible to the user. "
            "To show the user the result, send a concise summary.\n"
            "- The agent's outputs should generally be trusted\n"
            "- Clearly tell the agent whether you expect it to write code or "
            "just to do research\n\n"
            "## Writing the prompt\n\n"
            "Brief the agent like a smart colleague who just walked into the room.\n"
            "- Explain what you're trying to accomplish and why.\n"
            "- Describe what you've already learned or ruled out.\n"
            "- Give enough context for judgment calls.\n\n"
            "**Never delegate understanding.** Include concrete context such as "
            "file paths or specific changes when relevant."
        )

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        if self._spawn_fn is None:
            return "Error: Agent is not available in this context"

        prompt = (arguments.get("prompt") or "").strip()
        exp_name = (arguments.get("exp_name") or "").strip()

        if not prompt:
            return "Error: prompt is required and must not be empty"

        if self._valid_exp_names and (not exp_name or exp_name not in self._valid_exp_names):
            valid_list = ", ".join(sorted(self._valid_exp_names))
            return f"Error: exp_name must be one of: {valid_list}"

        return await self._spawn_fn(
            exp_name,
            prompt,
            self._cancel_token_for_exec(),
        )

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("AgentTool uses async execute() directly")
