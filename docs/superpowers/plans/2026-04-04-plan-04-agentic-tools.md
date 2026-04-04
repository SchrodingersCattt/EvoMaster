# Agentic Tools (Agent + TodoWrite + Skill) — Plan 04

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Agent (sub-agent spawn), TodoWrite (session task tracking), and Skill (skill dispatch) tools.

**Architecture:** Agent overrides `execute()` for native async spawn. TodoWrite uses file-backed store with `threading.Lock`. Skill migrates from `matmaster/tools/skill_tool.py` into `builtin/`, renamed to `Skill` with CC-aligned parameters.

**Tech Stack:** Python 3.10+, asyncio, threading, json, pathlib

**Spec:** `docs/superpowers/specs/2026-04-04-builtin-tools-design.md` — Section 5

**Depends on:** Plan 00 (infrastructure)

---

## CC Source Reference

### Agent
- **Name:** `Agent` (`tools/AgentTool/constants.ts:1`)
- **Schema** (`AgentTool.tsx:82-88`): `description: string`, `prompt: string`, `subagent_type?: string`, `model?: enum`, `run_in_background?: boolean`
- **Prompt:** `getPrompt()` (`prompt.ts:66-287`) — extensive: agent listing, usage notes, when not to use, writing prompts
- **MatMaster adaptation:** `subagent_type` → `exp_name` (domain alignment). Drop `model`, `run_in_background`, `isolation`, `name`, `team_name`, `mode`, `cwd`.

### TodoWrite
- **Name:** `TodoWrite` (`tools/TodoWriteTool/constants.ts`)
- **Schema** (`TodoWriteTool.ts:13-17`): `todos: TodoListSchema` where TodoItem = `{content: string, status: enum, activeForm: string}`
- **Prompt:** `PROMPT` (`prompt.ts:3-181`) — extensive usage guidance with examples
- **MatMaster adaptation:** Simplified TodoItem: `{id, content, status, priority?}`. File-backed instead of AppState. `activeForm` dropped (no UI spinner).

### Skill
- **Name:** `Skill` (`tools/SkillTool/constants.ts:1`)
- **Schema** (`SkillTool.ts` via `getPrompt()`): `skill: string`, `args?: string`
- **Prompt:** `getPrompt()` (`prompt.ts:173-196`) — how to invoke, skill listing, blocking requirement
- **MatMaster adaptation:** Migrated from `matmaster/tools/skill_tool.py`. Inherits BuiltinTool. Same core logic.

---

## Task 1: AgentTool

**Files:**
- Create: `matmaster/tools/builtin/agent_tool.py`
- Test: `tests/matmaster/tools/builtin/test_agent_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_agent_tool.py"""
import asyncio
import pytest
from matmaster.tools.builtin.agent_tool import AgentTool


class TestAgentToolMetadata:
    def test_name(self):
        assert AgentTool.name == "Agent"

    def test_stop_mode(self):
        assert AgentTool.stop_mode == "non_cancellable"


class TestAgentRecursionGuard:
    def test_no_spawn_fn_hidden_from_model(self):
        """Schema-layer guard: exposed_to_model=False when spawn_fn=None."""
        tool = AgentTool(spawn_fn=None)
        assert tool.exposed_to_model is False

    def test_no_spawn_fn_runtime_error(self):
        """Runtime-layer guard: returns error even if somehow called."""
        tool = AgentTool(spawn_fn=None)
        result = asyncio.run(tool.execute({
            "description": "test", "prompt": "do something",
        }))
        assert "error" in result.lower() or "not available" in result.lower()

    def test_with_spawn_fn_visible(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Result for: {task}"
        tool = AgentTool(spawn_fn=fake_spawn)
        assert tool.exposed_to_model is True

    def test_with_spawn_fn(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Result for: {task}"

        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(tool.execute({
            "description": "test", "prompt": "do something",
        }))
        assert "Result for: do something" in result

    def test_exp_name_passed_to_spawn(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Ran {exp_name}: {task}"

        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(tool.execute({
            "description": "test", "prompt": "do x", "exp_name": "explore",
        }))
        assert "explore" in result


class TestAgentDynamicSchema:
    def test_available_exps_modifies_schema(self):
        exps = [("explore", "Read-only exploration"), ("direct", "Full execution")]
        tool = AgentTool(spawn_fn=None, available_exps=exps)
        schema = tool.json_schema
        exp_prop = schema["properties"]["exp_name"]
        assert "enum" in exp_prop
        assert "explore" in exp_prop["enum"]
        assert "direct" in exp_prop["enum"]

    def test_description_includes_exps(self):
        exps = [("explore", "Read-only exploration")]
        tool = AgentTool(spawn_fn=None, available_exps=exps)
        assert "explore" in tool.description.lower()


class TestAgentValidation:
    def test_empty_prompt_error(self):
        async def fake_spawn(exp_name, task, stop_event=None):
            return "ok"
        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(tool.execute({
            "description": "test", "prompt": "",
        }))
        assert "error" in result.lower()
```

- [ ] **Step 2: Implement `agent_tool.py`**

```python
"""matmaster/tools/builtin/agent_tool.py

AgentTool — spawn a sub-agent to execute a specific task.

CC Reference: tools/AgentTool/ (constants.ts, prompt.ts, AgentTool.tsx)
CC name: Agent
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_spec import ResourceClaim


class AgentTool(BuiltinTool):
    """Spawn a sub-agent to execute a specific task.

    CC name: Agent (AgentTool)

    Recursion guard (two layers per spec):
    1. Schema-layer: spawn_fn=None → exposed_to_model=False (LLM never sees tool)
    2. Runtime-layer: execute() returns error when spawn_fn is None
    """

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
        spawn_fn: Callable[..., Awaitable[str]] | None = None,
        available_exps: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._spawn_fn = spawn_fn

        # Schema-layer recursion guard: hide from LLM when spawn not available
        if spawn_fn is None:
            self.exposed_to_model = False  # type: ignore[misc]

        if available_exps:
            self._apply_available_exps(available_exps)

    def _apply_available_exps(self, exps: list[tuple[str, str]]) -> None:
        names = [name for name, _ in exps]
        lines = [f"  - {name}: {desc}" for name, desc in exps if desc]
        if not lines:
            lines = [f"  - {name}" for name in names]
        exp_list_str = "\n".join(lines)

        self.description = (  # type: ignore[misc]
            "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
            "Provide a complete task description with all necessary context "
            "-- the sub-agent has no access to your conversation history.\n\n"
            f"Available sub-agent types:\n{exp_list_str}"
        )
        self.json_schema = {  # type: ignore[misc]
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
                    "description": (
                        "Sub-agent type:\n" + exp_list_str
                    ),
                },
            },
            "required": ["description", "prompt"],
        }

    def prompt(self, ctx=None) -> str:
        return (
            "Usage notes:\n"
            "- Always include a short description (3-5 words) summarizing what "
            "the agent will do\n"
            "- The result returned by the agent is not visible to the user. "
            "To show the user the result, send a text message with a concise summary.\n"
            "- The agent's outputs should generally be trusted\n"
            "- Clearly tell the agent whether you expect it to write code or "
            "just to do research\n\n"
            "## Writing the prompt\n\n"
            "Brief the agent like a smart colleague who just walked into the room.\n"
            "- Explain what you're trying to accomplish and why.\n"
            "- Describe what you've already learned or ruled out.\n"
            "- Give enough context for judgment calls.\n\n"
            "**Never delegate understanding.** Write prompts that prove you "
            "understood: include file paths, line numbers, what specifically to change."
        )

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        if self._spawn_fn is None:
            return (
                "Error: Agent is not available in this context "
                "(recursion depth limit reached)"
            )

        prompt = (arguments.get("prompt") or "").strip()
        exp_name = (arguments.get("exp_name") or "").strip()

        if not prompt:
            return "Error: prompt is required and must not be empty"

        try:
            return await self._spawn_fn(exp_name, prompt, self._cancel_token_for_exec())
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("AgentTool uses async execute() directly")
```

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest tests/matmaster/tools/builtin/test_agent_tool.py -v
git add matmaster/tools/builtin/agent_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_agent_tool.py
git commit -m "feat(tools): add AgentTool with spawn_fn and recursion guard"
```

---

## Task 2: TodoWriteTool

**Files:**
- Create: `matmaster/tools/builtin/todo_write_tool.py`
- Test: `tests/matmaster/tools/builtin/test_todo_write_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_todo_write_tool.py"""
import asyncio
import json
import pytest
from pathlib import Path
from matmaster.tools.builtin.todo_write_tool import TodoWriteTool
from matmaster.tools.tool_result import ToolResult


class TestTodoWriteMetadata:
    def test_name(self):
        assert TodoWriteTool.name == "TodoWrite"


class TestTodoWriteExecution:
    def test_create_todos(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        todos = [
            {"id": "1", "content": "First task", "status": "pending"},
            {"id": "2", "content": "Second task", "status": "in_progress"},
        ]
        result = asyncio.run(tool.execute({"todos": todos}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        # Verify file written
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 2

    def test_full_replacement(self, tmp_path):
        # Write initial
        (tmp_path / ".todos.json").write_text(json.dumps({
            "todos": [{"id": "1", "content": "Old", "status": "pending"}]
        }))
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(tool.execute({"todos": [
            {"id": "2", "content": "New", "status": "pending"},
        ]}))
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 1
        assert data["todos"][0]["id"] == "2"

    def test_all_completed_clears(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(tool.execute({"todos": [
            {"id": "1", "content": "Done", "status": "completed"},
        ]}))
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 0

    def test_invalid_status_error(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(tool.execute({"todos": [
            {"id": "1", "content": "Bad", "status": "invalid"},
        ]}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
```

- [ ] **Step 2: Implement `todo_write_tool.py`**

```python
"""matmaster/tools/builtin/todo_write_tool.py

TodoWriteTool — session task tracking with full-replacement semantics.

CC Reference: tools/TodoWriteTool/ (prompt.ts, TodoWriteTool.ts, utils/todo/types.ts)
CC name: TodoWrite
CC TodoItem: {content: string, status: enum, activeForm: string}
MatMaster TodoItem: {id: string, content: string, status: enum, priority?: string}
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim

VALID_STATUSES = {"pending", "in_progress", "completed"}
VALID_PRIORITIES = {"low", "medium", "high"}


class TodoWriteTool(BuiltinTool):
    """Update the todo list for the current session.

    CC name: TodoWrite (TodoWriteTool)
    """

    name: ClassVar[str] = "TodoWrite"
    description: ClassVar[str] = (
        "Update the todo list for the current session. To be used proactively "
        "and often to track progress and pending tasks."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique identifier"},
                        "content": {"type": "string", "description": "Task description"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Task status",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "Task priority (optional)",
                        },
                    },
                    "required": ["id", "content", "status"],
                },
                "description": "The updated todo list (full replacement)",
            },
        },
        "required": ["todos"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="todo-store", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"task.write"})
    effect_level: ClassVar[str] = "local_mutation"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()  # instance-level, not class-level

    def prompt(self, ctx=None) -> str:
        return (
            "Use this tool to create and manage a structured task list for your "
            "current session. This helps you track progress and organize complex tasks.\n\n"
            "## When to Use\n"
            "- Complex multi-step tasks (3+ steps)\n"
            "- User provides multiple tasks\n"
            "- When starting work on a task (mark in_progress)\n"
            "- After completing a task (mark completed)\n\n"
            "## When NOT to Use\n"
            "- Single, straightforward tasks\n"
            "- Purely conversational requests\n\n"
            "## Task Management\n"
            "- Update status in real-time as you work\n"
            "- Mark tasks complete IMMEDIATELY after finishing\n"
            "- Only ONE task should be in_progress at a time"
        )

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        if self._workdir is None:
            return ToolResult(status="error", content="Error: workdir not available")

        todos = arguments.get("todos", [])

        # Validate
        for todo in todos:
            if not isinstance(todo, dict):
                return ToolResult(status="error", content="Error: each todo must be an object")
            for field in ("id", "content", "status"):
                if field not in todo:
                    return ToolResult(status="error", content=f"Error: todo missing required field '{field}'")
            if todo["status"] not in VALID_STATUSES:
                return ToolResult(
                    status="error",
                    content=f"Error: invalid status '{todo['status']}'. Must be one of: {VALID_STATUSES}",
                )
            if "priority" in todo and todo["priority"] not in VALID_PRIORITIES:
                return ToolResult(
                    status="error",
                    content=f"Error: invalid priority '{todo['priority']}'. Must be one of: {VALID_PRIORITIES}",
                )

        path = Path(self._workdir) / ".todos.json"

        with self._lock:
            # Read old
            old_todos: list[dict] = []
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    old_todos = data.get("todos", [])
                except Exception:
                    pass

            # All completed → clear
            all_done = todos and all(t["status"] == "completed" for t in todos)
            new_todos = [] if all_done else todos

            # Write
            path.write_text(
                json.dumps({"todos": new_todos}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Compute summary (spec: added N, updated N, removed N, completed N)
        old_ids = {t.get("id") for t in old_todos}
        new_ids = {t.get("id") for t in todos}
        added = len(new_ids - old_ids)
        removed = len(old_ids - new_ids)
        updated = len(old_ids & new_ids)
        completed = sum(1 for t in todos if t["status"] == "completed")

        summary = f"Todos updated: {added} added, {updated} updated, {removed} removed, {completed} completed"
        if all_done:
            summary += " (all done, list cleared)"

        return ToolResult(status="success", content=summary)
```

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest tests/matmaster/tools/builtin/test_todo_write_tool.py -v
git add matmaster/tools/builtin/todo_write_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_todo_write_tool.py
git commit -m "feat(tools): add TodoWriteTool with full-replacement semantics"
```

---

## Task 3: SkillTool

**Files:**
- Create: `matmaster/tools/builtin/skill_tool.py` (new, migrated from `matmaster/tools/skill_tool.py`)
- Test: `tests/matmaster/tools/builtin/test_skill_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_skill_tool.py"""
import asyncio
import pytest
from unittest.mock import MagicMock
from matmaster.tools.builtin.skill_tool import SkillTool


def make_skill(name="test-skill", body="# Test Skill\nDo things.", mcp=None, deps=None):
    skill = MagicMock()
    skill.get_full_info.return_value = body
    skill.skill_path.resolve.return_value = "/skills/test-skill"
    skill.meta_info.mcp_server = mcp
    skill.meta_info.depends_on = deps or []
    return skill


def make_registry(skill=None):
    reg = MagicMock()
    reg.get_skill.return_value = skill
    return reg


class TestSkillToolMetadata:
    def test_name(self):
        assert SkillTool.name == "Skill"

    def test_schema_has_skill_param(self):
        tool = SkillTool(skill_registry=make_registry())
        assert "skill" in tool.json_schema["properties"]

    def test_schema_has_args_param(self):
        tool = SkillTool(skill_registry=make_registry())
        assert "args" in tool.json_schema["properties"]


class TestSkillExecution:
    def test_skill_not_found(self):
        tool = SkillTool(skill_registry=make_registry(skill=None))
        result = asyncio.run(tool.execute({"skill": "nonexistent"}))
        assert "error" in result.lower()

    def test_skill_found(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "test-skill"}))
        assert "Test Skill" in result
        assert "/skills/test-skill" in result

    def test_mcp_hit_callback(self):
        skill = make_skill(mcp="my-server")
        callback = MagicMock()
        tool = SkillTool(
            skill_registry=make_registry(skill=skill),
            on_skill_hit=callback,
        )
        asyncio.run(tool.execute({"skill": "test-skill"}))
        callback.assert_called_with("my-server")

    def test_args_appended(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "test-skill", "args": "some args"}))
        assert "some args" in result

    def test_slash_prefix_stripped(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "/test-skill"}))
        assert "Test Skill" in result

    def test_no_registry_error(self):
        tool = SkillTool(skill_registry=None)
        result = asyncio.run(tool.execute({"skill": "test-skill"}))
        assert "error" in result.lower()
```

- [ ] **Step 2: Implement `skill_tool.py` in builtin/**

```python
"""matmaster/tools/builtin/skill_tool.py

SkillTool — activate a skill by name, returning its full documentation.

CC Reference: tools/SkillTool/ (constants.ts, prompt.ts, SkillTool.ts)
CC name: Skill
CC Schema: {skill: string, args?: string}

Migrated from matmaster/tools/skill_tool.py. Now inherits BuiltinTool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

if TYPE_CHECKING:
    from matmaster.skills.registry import Skill, SkillRegistry

logger = logging.getLogger(__name__)


class SkillTool(BuiltinTool):
    """Activate a skill by name and return its full documentation.

    CC name: Skill (SkillTool)
    """

    name: ClassVar[str] = "Skill"
    description: ClassVar[str] = (
        "Execute a skill within the main conversation. "
        "When users reference a slash command or /<something>, "
        "they are referring to a skill. Use this tool to invoke it."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name. E.g., \"commit\", \"review-pr\", or \"pdf\"",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments for the skill",
            },
        },
        "required": ["skill"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset({"skill.dispatch"})
    effect_level: ClassVar[str] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        skill_registry: SkillRegistry | None = None,
        on_skill_hit: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._registry = skill_registry
        self._on_skill_hit = on_skill_hit

    def prompt(self, ctx=None) -> str:
        return (
            "Execute a skill within the main conversation\n\n"
            "When users ask you to perform tasks, check if any of the available "
            "skills match. Skills provide specialized capabilities and domain knowledge.\n\n"
            "When users reference a \"slash command\" or \"/<something>\" "
            "(e.g., \"/commit\", \"/review-pr\"), they are referring to a skill. "
            "Use this tool to invoke it.\n\n"
            "How to invoke:\n"
            "- Use this tool with the skill name and optional arguments\n"
            "- Examples:\n"
            "  - `skill: \"pdf\"` - invoke the pdf skill\n"
            "  - `skill: \"commit\", args: \"-m 'Fix bug'\"` - invoke with arguments\n\n"
            "Important:\n"
            "- Available skills are listed in system-reminder messages\n"
            "- When a skill matches the user's request, invoke it BEFORE generating "
            "any other response\n"
            "- NEVER mention a skill without actually calling this tool\n"
            "- Do not invoke a skill that is already running"
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        """Native async execute — bypasses _execute + to_thread (per spec 5.3)."""
        try:
            skill_name = (arguments.get("skill") or "").lstrip("/")  # strip slash prefix (CC behavior)
            args = arguments.get("args", "")

            if self._registry is None:
                return "Error: skill registry not available"

            skill = self._registry.get_skill(skill_name)
            if skill is None:
                return f"Error: Skill '{skill_name}' not found"

            body = skill.get_full_info()
            skill_dir = str(skill.skill_path.resolve())
            body = body.replace("${SKILL_DIR}", skill_dir)

            self._maybe_hit_mcp(skill)

            for dep_name in skill.meta_info.depends_on:
                dep_skill = self._registry.get_skill(dep_name)
                if dep_skill is not None:
                    self._maybe_hit_mcp(dep_skill)

            result = f"Base directory for this skill: {skill_dir}\n\n{body}"
            if args:
                result += f"\n\nARGUMENTS: {args}"
            return result
        except Exception as e:
            logger.error("Skill tool failed: %s", e, exc_info=True)
            return f"Error: {e}"

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str:
        # TODO: pass cancel_token when needed
        return await self.execute(arguments)

    def _maybe_hit_mcp(self, skill: Skill) -> None:
        mcp_server = skill.meta_info.mcp_server
        if mcp_server and self._on_skill_hit:
            self._on_skill_hit(mcp_server)

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("SkillTool uses async execute() directly")
```

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest tests/matmaster/tools/builtin/test_skill_tool.py -v
git add matmaster/tools/builtin/skill_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_skill_tool.py
git commit -m "feat(tools): add SkillTool migrated to builtin/ with CC naming"
```
