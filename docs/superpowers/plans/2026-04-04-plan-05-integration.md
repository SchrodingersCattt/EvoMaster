# Integration & Test Updates — Plan 05

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire new builtin tools into Exp, update all downstream references to use new tool names, update TOML configs, fix existing tests.

**Architecture:** Rewire `Exp._init_builtin_tools()` to use new tool classes with session/sessionless split. Update all hardcoded tool name references across the codebase.

**Tech Stack:** Python 3.10+, TOML

**Spec:** `docs/superpowers/specs/2026-04-04-builtin-tools-design.md` — Section 6

**Depends on:** Plans 00-04 (all tools implemented)

---

## Task 1: Rewrite `Exp._init_builtin_tools()`

**Files:**
- Modify: `matmaster/core/exp.py`

- [ ] **Step 1: Update imports and tool registration**

In `_init_builtin_tools()` (line ~461), replace all old imports with:

```python
from matmaster.tools.builtin import (
    BashTool, ReadTool, WriteTool, EditTool,
    GlobTool, GrepTool, WebSearchTool, WebFetchTool,
    TodoWriteTool,
)
```

Replace the `native_tools` list with session/sessionless split:

```python
# Session-dependent tools
if ctx.session is not None:
    session_tools = [
        BashTool(session=ctx.session, workdir=exec_wd),
        ReadTool(session=ctx.session, workdir=exec_wd),
        WriteTool(session=ctx.session, workdir=exec_wd),
        EditTool(session=ctx.session, workdir=exec_wd),
        GlobTool(session=ctx.session, workdir=exec_wd),
        GrepTool(session=ctx.session, workdir=exec_wd),
    ]
    for tool in session_tools:
        if _want(tool.name):
            registry.register(tool, source='builtin')

# Session-independent tools (always registered)
sessionless_tools = [
    TodoWriteTool(workdir=ctx.workdir),
    WebSearchTool(),
    WebFetchTool(workdir=ctx.workdir),
]
for tool in sessionless_tools:
    if _want(tool.name):
        registry.register(tool, source='builtin')
```

- [ ] **Step 2: Comment out MonitorJobTool**

```python
# TODO: rebuild MonitorJobTool
# from matmaster.tools.builtin.monitor_job import MonitorJobTool
```

- [ ] **Step 3: Update Agent registration**

Replace the SpawnTool block (~line 256) with:

```python
if ("Agent" in builtin_cfg or "*" in builtin_cfg) and ctx.session is not None:
    from matmaster.tools.builtin import AgentTool
    agent_tool = AgentTool(
        session=ctx.session, workdir=exec_wd,
        spawn_fn=spawn_fn, available_exps=available_exps,
    )
    registry.register(agent_tool, source="builtin")
```

- [ ] **Step 4: Update `_derive_active_planes()`**

Change line ~190:
```python
# Old: for name in ("mm_web_search", "web_fetch", "monitor_job")
for name in ("WebSearch", "WebFetch")
```

- [ ] **Step 5: Update `_init_skill_tools()` to use new SkillTool**

Change the import from `matmaster.tools.skill_tool` to `matmaster.tools.builtin.skill_tool`:
```python
from matmaster.tools.builtin.skill_tool import SkillTool
```

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/exp.py
git commit -m "refactor(exp): rewire _init_builtin_tools to new CC-named tool classes"
```

---

## Task 2: Update `capability_policy.py`

**Files:**
- Modify: `matmaster/core/capability_policy.py`

- [ ] **Step 1: Update tool name check**

Line 124: `"execute_bash"` → `"Bash"`
Lines 92-93: update comments

```python
# Old: if tool_name == "execute_bash":
if tool_name == "Bash":
    return self.check_bash_safety(tool_args)
```

- [ ] **Step 2: Commit**

```bash
git add matmaster/core/capability_policy.py
git commit -m "refactor: update capability_policy Bash tool name reference"
```

---

## Task 3: Update `agent.py` (AgentKernel)

**Files:**
- Modify: `matmaster/core/agent.py`

- [ ] **Step 1: Update SkillHitEvent check**

Line ~409:
```python
# Old: if tc.name == "use_skill":
#          skill_name = tc.arguments.get("skill_name")
if tc.name == "Skill":
    skill_name = tc.arguments.get("skill")
```

- [ ] **Step 2: Commit**

```bash
git add matmaster/core/agent.py
git commit -m "refactor: update agent.py Skill tool name and param references"
```

---

## Task 4: Update `tool_compiler.py`

**Files:**
- Modify: `matmaster/tools/tool_compiler.py`

- [ ] **Step 1: Update relaxation list**

Line 36:
```python
# Old: tool.name in ("list_dir", "glob", "grep")
tool.name in ("Glob", "Grep")
```

- [ ] **Step 2: Commit**

```bash
git add matmaster/tools/tool_compiler.py
git commit -m "refactor: update tool_compiler relaxation list to new tool names"
```

---

## Task 5: Update `eval_tooling_snapshot.py`

**Files:**
- Modify: `matmaster/eval_tooling_snapshot.py`

- [ ] **Step 1: Update `_BUILTIN_WHEN_STAR`**

```python
_BUILTIN_WHEN_STAR: list[str] = [
    "Bash", "Read", "Write", "Edit",
    "Glob", "Grep", "TodoWrite",
    "WebSearch", "WebFetch",
]
```

- [ ] **Step 2: Update spawn and skill references**

Line 46: `+ ["spawn"]` → `+ ["Agent"]`
Line 136: `surface_tools.append("use_skill")` → `surface_tools.append("Skill")`

- [ ] **Step 3: Commit**

```bash
git add matmaster/eval_tooling_snapshot.py
git commit -m "refactor: update eval_tooling_snapshot to new tool names"
```

---

## Task 6: Update `devshell/runner.py`

**Files:**
- Modify: `matmaster/devshell/runner.py`

- [ ] **Step 1: Update description text**

Line 72: `"execute_bash"` → `"Bash"` in the description string.

- [ ] **Step 2: Commit**

```bash
git add matmaster/devshell/runner.py
git commit -m "refactor: update devshell runner Bash tool name reference"
```

---

## Task 7: Update TOML configs

**Files:**
- Modify: `matmaster/exps/direct.toml`
- Modify: `matmaster/exps/explore.toml`

- [ ] **Step 1: Update `direct.toml`**

```toml
[tools]
builtin = [
    "Bash", "Read", "Write", "Edit",
    "Glob", "Grep", "TodoWrite",
    "Agent", "WebSearch", "WebFetch",
]
```

- [ ] **Step 2: Update `explore.toml`**

Update builtin list:
```toml
builtin = [
    "Bash", "Read", "Glob", "Grep",
    "WebSearch", "WebFetch",
]
```

Update developer_instructions text: replace `read_file` → `Read`, `list_dir` → `Glob`, `execute_bash` → `Bash`, `mm_web_search` → `WebSearch`, `web_fetch` → `WebFetch`.

- [ ] **Step 3: Commit**

```bash
git add matmaster/exps/direct.toml matmaster/exps/explore.toml
git commit -m "refactor: update TOML configs to new CC-aligned tool names"
```

---

## Task 8: Update existing test files

**Files to update** (old tool name references):
- `tests/matmaster/core/test_exp.py`
- `tests/matmaster/core/test_agent_kernel_stream.py`
- `tests/matmaster/core/test_exp_skills.py`
- `tests/matmaster/core/test_structural_validation.py`
- `tests/matmaster/core/test_capability_policy.py`
- `tests/matmaster/tools/test_tool_compiler.py`
- `tests/matmaster/devshell/test_integration.py`
- `tests/matmaster/test_eval_tooling_snapshot.py`
- `tests/test_adapt_tool_calls_format.py`

- [ ] **Step 1: Search and replace old tool names in test files**

For each file, replace:
- `"execute_bash"` → `"Bash"`
- `"read_file"` → `"Read"`
- `"write_file"` → `"Write"`
- `"edit_file"` → `"Edit"`
- `"glob"` → `"Glob"` (careful: only in tool name contexts)
- `"grep"` → `"Grep"` (careful: only in tool name contexts)
- `"mm_web_search"` → `"WebSearch"`
- `"web_fetch"` → `"WebFetch"`
- `"list_dir"` → remove or replace with `"Glob"`
- `"spawn"` → `"Agent"`
- `"use_skill"` → `"Skill"`
- `"skill_name"` → `"skill"` (in Skill tool argument contexts)
- `"task_create"` / `"task_get"` / `"task_list"` / `"task_update"` / `"task_complete"` → `"TodoWrite"` or remove

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | head -100
```

Fix any remaining failures.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "refactor: update all test files to new CC-aligned tool names"
```

---

## Task 9: Remove old `matmaster/tools/skill_tool.py`

**Files:**
- Delete: `matmaster/tools/skill_tool.py` (replaced by `matmaster/tools/builtin/skill_tool.py`)

- [ ] **Step 1: Verify no other imports reference old path**

```bash
grep -r "from matmaster.tools.skill_tool" matmaster/ tests/ --include="*.py"
```

Update any remaining references to point to `matmaster.tools.builtin.skill_tool`.

- [ ] **Step 2: Delete and commit**

```bash
git rm matmaster/tools/skill_tool.py
git commit -m "refactor: remove old skill_tool.py (migrated to builtin/)"
```

---

## Task 10: Final verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 2: Verify tool import chain**

```bash
python -c "
from matmaster.tools.builtin import (
    BuiltinTool, BashTool, ReadTool, EditTool, WriteTool,
    GlobTool, GrepTool, WebSearchTool, WebFetchTool,
    AgentTool, TodoWriteTool, SkillTool,
)
for cls in [BashTool, ReadTool, EditTool, WriteTool, GlobTool, GrepTool,
            WebSearchTool, WebFetchTool, AgentTool, TodoWriteTool, SkillTool]:
    print(f'{cls.name}: OK')
"
```

- [ ] **Step 3: Commit any remaining fixes**

```bash
git add -A
git commit -m "feat: complete builtin tools rebuild with CC naming convention"
```
