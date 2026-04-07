"""AgentTool -- spawn a sub-agent to execute a specific task."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar

from matmaster.config.exp import ExpSubagentMeta
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext

SpawnFn = Callable[[str, str, CancellationToken | None], Awaitable[str]]


class AgentTool(BuiltinTool):
    """Spawn a sub-agent to execute a specific task."""

    name: ClassVar[str] = "Agent"
    description: ClassVar[str] = (
        "Launch a subagent defined in matmaster/exps to handle a complex task."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "exp_name": {
                "type": "string",
                "description": "The exp-backed subagent type to launch",
            },
            "task_summary": {
                "type": "string",
                "description": "Optional short label for this delegated task",
            },
            "description": {
                "type": "string",
                "description": "Deprecated alias for task_summary",
                "deprecated": True,
            },
            "prompt": {
                "type": "string",
                "description": "Complete task briefing for the subagent",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
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
        available_exps: list[ExpSubagentMeta] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._spawn_fn = spawn_fn
        self._available_exps = tuple(available_exps or ())
        self._meta_by_name = {meta.name: meta for meta in self._available_exps}
        self._valid_exp_names = frozenset(self._meta_by_name)

        if spawn_fn is None:
            self.exposed_to_model = False

        if self._available_exps:
            self._apply_available_exps(self._available_exps)

    def _apply_available_exps(
        self,
        exps: tuple[ExpSubagentMeta, ...],
    ) -> None:
        self.json_schema = {
            "type": "object",
            "properties": {
                "exp_name": {
                    "type": "string",
                    "enum": [meta.name for meta in exps],
                    "description": "The exp-backed subagent type to launch",
                },
                "task_summary": {
                    "type": "string",
                    "description": "Optional short label for this delegated task",
                },
                "description": {
                    "type": "string",
                    "description": "Deprecated alias for task_summary",
                    "deprecated": True,
                },
                "prompt": {
                    "type": "string",
                    "description": "Complete task briefing for the subagent",
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        }

    def prompt(self, ctx=None) -> str:
        available_lines = [
            f"- {meta.name}: {meta.when_to_use or meta.description} "
            f"(Tools: {meta.tools_summary}; "
            f"{'read-only' if meta.read_only else 'read-write'})"
            for meta in self._available_exps
        ]
        if not available_lines:
            available_lines = [
                "- No model-visible subagent definitions are currently available.",
            ]

        lines = [
            "Launch a new agent to handle complex, multi-step tasks autonomously.",
            "",
            "Available subagent types and the tools they have access to:",
            *available_lines,
            "",
            "When NOT to use the Agent tool:",
            "- If you want to read a specific file path, use Read instead of Agent.",
            "- If you are searching within a specific file or a set of 2-3 files, use Read or Grep instead of Agent.",
            "- If you only need a quick path lookup, use Glob instead of Agent.",
            "- If the task does not match any subagent type listed above, do not use Agent.",
            "",
            "Usage notes:",
            "- Always include a short task summary when possible.",
            "- When the subagent is done, the result comes back to you, not directly to the user. If the user should see it, send a concise summary yourself.",
            "- The subagent's outputs should generally be trusted.",
            "- Clearly tell the subagent whether you expect it to write code or just to do research.",
            "- If you want parallel delegation, send multiple Agent tool calls in a single assistant message.",
            "- Each Agent invocation starts fresh, so the prompt must include all needed context.",
            "- Intermediate subagent events are forwarded with a spawn_id for streaming and replay, but the parent agent should treat the final tool result as the authoritative completion payload.",
            "- MatMaster does not currently support background tasks, fork-style context inheritance, or resuming a prior subagent. Do not imply those capabilities.",
            "",
            "## Writing the prompt",
            "",
            "Brief the agent like a smart colleague who just walked into the room.",
            "- Explain what you're trying to accomplish and why.",
            "- Describe what you've already learned or ruled out.",
            "- Give enough context about the surrounding problem that the agent can make judgment calls instead of following a narrow instruction.",
            '- If you need a short response, say so (for example: "report in under 200 words").',
            "- Lookups: hand over the exact command. Investigations: hand over the question.",
            "- Terse command-style prompts produce shallow, generic work.",
            "",
            "Never delegate understanding.",
            "- Do not write prompts like based on your findings, fix the bug. Include the relevant file paths, suspected cause, and what specifically should change.",
        ]
        return "\n".join(lines)

    def _default_exp_name(self) -> str:
        if "direct" in self._valid_exp_names:
            return "direct"
        if self._valid_exp_names:
            return sorted(self._valid_exp_names)[0]
        return ""

    def _normalize_arguments(
        self,
        arguments: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        allowed = {"exp_name", "task_summary", "prompt", "description"}
        extra = set(arguments) - allowed
        if extra:
            return None, f"unsupported Agent arguments: {', '.join(sorted(extra))}"

        exp_name = (
            str(arguments.get("exp_name", "")).strip() or self._default_exp_name()
        )
        prompt = str(arguments.get("prompt", "")).strip()
        task_summary = str(
            arguments.get("task_summary") or arguments.get("description") or ""
        ).strip()

        if self._valid_exp_names and exp_name not in self._valid_exp_names:
            valid_list = ", ".join(sorted(self._valid_exp_names))
            return None, f"exp_name must be one of: {valid_list}"
        if not prompt:
            return None, "prompt is required and must not be empty"

        return {
            "exp_name": exp_name,
            "task_summary": task_summary,
            "prompt": prompt,
        }, None

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        normalized, error = self._normalize_arguments(arguments)
        if error:
            return ToolDecision(decision="deny", reason=error)
        return ToolDecision(decision="allow", modified_args=normalized)

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        if self._spawn_fn is None:
            return "Error: Agent is not available in this context"

        normalized, error = self._normalize_arguments(arguments)
        if error:
            return f"Error: {error}"

        assert normalized is not None
        result = await self._spawn_fn(
            normalized["exp_name"],
            normalized["prompt"],
            self._cancel_token_for_exec(),
        )
        return ToolResult(
            status="success",
            content=result,
            payload={
                "exp_name": normalized["exp_name"],
                "task_summary": normalized["task_summary"],
                "prompt": normalized["prompt"],
            },
        )

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        """Context-aware execution — delegates to async execute()."""
        return await self.execute(arguments)

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("AgentTool uses async execute() directly")
