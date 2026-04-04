# Builtin Tools Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-implemented CC-named builtin tools into the runtime, migrate remaining legacy tool-name/path references, and remove the last compatibility module without redoing tool-module work from Plans 01-04.

**Architecture:** The worktree already contains the rebuilt `matmaster.tools.builtin` modules and final package exports. This plan focuses only on the integration layer: `Exp` wiring, downstream name-based logic, exp configs, tests, and model-visible docs. Delete `matmaster/tools/skill_tool.py` only after code, tests, and docs stop depending on the legacy name/path surface.

**Tech Stack:** Python 3.10+, pytest, TOML, ripgrep, Pydantic models

---

## Current State

- Already done in this worktree:
  - `matmaster/tools/builtin/__init__.py` exports `BashTool`, `ReadTool`, `WriteTool`, `EditTool`, `GlobTool`, `GrepTool`, `AgentTool`, `TodoWriteTool`, `WebSearchTool`, `WebFetchTool`, and `SkillTool`.
  - The rebuilt tool modules already exist under `matmaster/tools/builtin/`.
- Still stale in this worktree:
  - `matmaster/core/exp.py` still imports/registers legacy `ListDirTool`, `Task*Tool`, `SpawnTool`, and old `matmaster.tools.skill_tool`.
  - `build_runtime()` still skips `_init_builtin_tools()` when `ctx.session is None`, which blocks sessionless tools.
  - `capability_policy.py`, `agent.py`, `tool_compiler.py`, `eval_tooling_snapshot.py`, `devshell/runner.py`, `explore.toml`, and several tests still use old tool names.
  - `matmaster/tools/skill_tool.py` still exists and is still imported by runtime/tests.
  - The repo is currently hybrid and broken, not just stale: `exp.py` and `tests/matmaster/tools/test_tool_descriptions.py` still reference modules that are no longer present under `matmaster/tools/builtin/`.
- Non-goals for this plan:
  - Do not redesign `glob_tool.py`, `web_fetch_tool.py`, or `agent_tool.py` behavior here; those open Plan 01-04 findings stay separate.
  - Do not rename session protocol methods such as `session.read_file()` / `session.write_file()`; only rename tool IDs, config entries, prompt text, and tool-call payload keys.

## File Map

- `matmaster/core/exp.py`: session/sessionless builtin registration, Agent recursion guard, Skill import/call path, active-plane derivation.
- `matmaster/core/capability_policy.py`: `Bash` safety dispatch.
- `matmaster/core/agent.py`: `SkillHitEvent` trigger for `Skill` / `skill`.
- `matmaster/tools/tool_compiler.py`: stateless-local shell-claim relaxation for `Glob` / `Grep`.
- `matmaster/eval_tooling_snapshot.py`: devshell snapshot names for builtin surface and `Skill`.
- `matmaster/devshell/runner.py`: local-session hint text mentioning `Bash`.
- `matmaster/adaptors/calculation/job_service.py`, `matmaster/integration/bohrium_env.py`: follow-up audit if `monitor_job` is truly removed from the builtin runtime.
- `matmaster/exps/direct.toml`: intentionally expose rebuilt builtin tools for direct agent if spec 6.2 remains the source of truth.
- `matmaster/exps/explore.toml`: rename builtin list and developer instructions to CC names.
- `matmaster/tools/skill_tool.py`: delete only after all imports move to `builtin/skill_tool.py`.
- `tests/matmaster/core/test_exp.py`, `tests/matmaster/core/test_exp_runtime_v2.py`, `tests/matmaster/core/test_exp_skills.py`, `tests/matmaster/core/test_hook_wiring.py`: runtime wiring regression coverage.
- `tests/matmaster/core/test_exp.py`: also contains legacy expectations about the old 15-tool builtin set (`task_*`, `monitor_job`, `execute_bash`, `list_dir`, `mm_web_search`) and must be audited before registration changes land.
- `tests/matmaster/core/test_capability_policy.py`, `tests/matmaster/tools/test_tool_compiler.py`, `tests/matmaster/test_eval_tooling_snapshot.py`, `tests/matmaster/devshell/test_integration.py`, `tests/matmaster/core/test_agent_kernel_stream.py`, `tests/matmaster/tools/test_tool_descriptions.py`, `tests/test_skill_tool.py`, `tests/matmaster/tools/builtin/test_skill_tool.py`: downstream name/path migration coverage.
- `matmaster/skills/playground-skills/**/SKILL.md` and prompt markdown files listed in Task 5: model-visible legacy tool-name references.

## Chunk 1: Runtime Wiring

### Task 1: Rewire `Exp` entry points for sessionless builtins and spawn guard

**Files:**
- Modify: `matmaster/core/exp.py`
- Test: `tests/matmaster/core/test_exp.py`
- Test: `tests/matmaster/core/test_exp_runtime_v2.py`
- Test: `tests/matmaster/core/test_hook_wiring.py`

- [ ] **Step 1: Write failing tests for the new `Exp` runtime contract**

Add these tests:

```python
# tests/matmaster/core/test_exp.py
@pytest.mark.asyncio
async def test_build_runtime_registers_todowrite_without_session(tmp_path: Path) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["TodoWrite"]),
        )
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=None,
        llm_provider=MockLLMProvider(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert runtime.spec.tool_catalog.get_tool("TodoWrite") is not None


@pytest.mark.asyncio
async def test_build_runtime_hides_agent_when_allow_spawn_false(tmp_path: Path) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Agent"]),
        ),
        allow_spawn=False,
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=MagicMock(spec=Session),
        llm_provider=MockLLMProvider(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    tool = runtime.spec.tool_catalog.get_tool("Agent")
    assert tool is not None
    assert tool.tool_spec.exposed_to_model is False
```

```python
# tests/matmaster/core/test_exp_runtime_v2.py
@pytest.mark.asyncio
async def test_build_runtime_adds_external_service_plane_for_websearch(
    tmp_path: Path,
) -> None:
    from matmaster.config.exp import ExpConfig
    from matmaster.core.exp import Exp
    from matmaster.types.context import PlaygroundContext
    from matmaster.types.topology import ToolPlane

    config = ExpConfig(name="test", tools={"builtin": ["WebSearch"]})
    exp = Exp(config)
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=None,
        llm_provider=_MockProvider(),
    )

    runtime = await exp.build_runtime(ctx)

    assert ToolPlane.EXTERNAL_SERVICE in runtime.spec.runtime_topology.active_planes
```

```python
# tests/matmaster/core/test_hook_wiring.py
@pytest.mark.asyncio
async def test_make_spawn_fn_constructs_child_exp_with_allow_spawn_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import matmaster.core.exp as exp_module

    created_allow_spawn: list[bool] = []
    original_exp = exp_module.Exp

    class RecordingExp(original_exp):
        def __init__(self, config, *, allow_spawn: bool = True) -> None:
            created_allow_spawn.append(allow_spawn)
            super().__init__(config, allow_spawn=allow_spawn)

        async def run_stream(self, *args, **kwargs):
            if False:
                yield None

    async def fake_drain_run_stream(_stream):
        return SimpleNamespace(
            status="completed",
            final_content="child done",
            reason="natural",
        )

    monkeypatch.setattr(exp_module, "Exp", RecordingExp)

    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        run_meta={"session_id": "session-1"},
        llm_provider=MockLLMProvider(),
    )

    with patch(
        "matmaster.config.loader.load_exp_config",
        return_value=ExpConfig(name="direct"),
    ), patch(
        "matmaster.core.stream_drain.drain_run_stream",
        side_effect=fake_drain_run_stream,
    ):
        spawn_fn = original_exp._make_spawn_fn(
            ctx,
            source_prefix="MatMaster",
            hook_executor=HookExecutor(),
        )
        result = await spawn_fn("direct", "summarize this task")

    assert result == "child done"
    assert created_allow_spawn[-1] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -m pytest tests/matmaster/core/test_exp.py::test_build_runtime_registers_todowrite_without_session tests/matmaster/core/test_exp.py::test_build_runtime_hides_agent_when_allow_spawn_false tests/matmaster/core/test_exp_runtime_v2.py::test_build_runtime_adds_external_service_plane_for_websearch tests/matmaster/core/test_hook_wiring.py::test_make_spawn_fn_constructs_child_exp_with_allow_spawn_false -v`

Expected: FAIL because `build_runtime()` skips sessionless builtins, `_derive_active_planes()` still uses old names, and child `Exp` still defaults `allow_spawn=True`.

- [ ] **Step 3: Implement the `Exp` entry-point changes**

Update `matmaster/core/exp.py`:

```python
class Exp:
    def __init__(self, config: ExpConfig, *, allow_spawn: bool = True) -> None:
        self._config = config
        self._allow_spawn = allow_spawn
        self._cleanup_callbacks = []
        self._skill_registry = None
        self.logger = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _make_spawn_fn(...):
        async def spawn_fn(...):
            ...
            child_config = load_exp_config(exp_name)
            child_exp = Exp(child_config, allow_spawn=False)
            ...

    async def build_runtime(...):
        registry = ToolRegistry()
        builtin_cfg = self._config.tools.builtin
        if builtin_cfg:
            self._init_builtin_tools(ctx, registry, builtin_cfg)
        ...

    @staticmethod
    def _derive_active_planes(...):
        ...
        if skills_enabled or any(
            name in builtin_cfg or "*" in builtin_cfg
            for name in ("WebSearch", "WebFetch")
        ):
            planes.add(ToolPlane.EXTERNAL_SERVICE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2.

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/exp.py \
        tests/matmaster/core/test_exp.py \
        tests/matmaster/core/test_exp_runtime_v2.py \
        tests/matmaster/core/test_hook_wiring.py
git commit -m "refactor(exp): allow sessionless builtins and child spawn guard"
```

### Task 2: Rewrite builtin registration and move `Exp` onto builtin `SkillTool`

**Files:**
- Modify: `matmaster/core/exp.py`
- Test: `tests/matmaster/core/test_exp_skills.py`
- Test: `tests/matmaster/core/test_exp.py`

- [ ] **Step 1: Inventory the legacy builtin-set expectations before editing**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && rg -n 'monitor_job|task_create|task_get|task_list|task_update|task_complete|mm_web_search|execute_bash|list_dir|len\\(registry\\)' tests/matmaster/core/test_exp.py`

Expected: hits for the old 15-tool registry assumptions. Rewrite those expectations in the same task as the registration change; do not leave `test_exp.py` asserting the pre-CC builtin surface.

- [ ] **Step 2: Write failing tests for the new registration surface**

Update `tests/matmaster/core/test_exp_skills.py`:

```python
def test_skill_tools_registered_when_enabled(self, tmp_path):
    ...
    exp._init_skill_tools(ctx, registry)

    assert "Skill" in registry

    from matmaster.tools.builtin.skill_tool import SkillTool as BuiltinSkillTool

    skill_tool = registry._tools["Skill"]
    assert isinstance(skill_tool, BuiltinSkillTool)


async def test_skill_trigger_injects_lazy_tools(self, tmp_path):
    ...
    exp._init_skill_tools(ctx, registry)

    assert "Skill" in registry
    skill_tool = registry._tools["Skill"]
    raw_result = await skill_tool.execute({"skill": "test-skill"})
    result = normalize_tool_result(raw_result)
    assert result.status == "success"
    assert "mat_sg_build_bulk" in registry
```

Add one `tests/matmaster/core/test_exp.py` assertion that `Agent` registers with the new name when enabled, and remove the old assertions that still expect `task_*`, `monitor_job`, `execute_bash`, `list_dir`, or `mm_web_search` to be present by default:

```python
@pytest.mark.asyncio
async def test_build_runtime_registers_agent_by_cc_name(tmp_path: Path) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Agent"]),
        )
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=MagicMock(spec=Session),
        llm_provider=MockLLMProvider(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert runtime.spec.tool_catalog.get_tool("Agent") is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -m pytest tests/matmaster/core/test_exp.py::test_build_runtime_registers_agent_by_cc_name tests/matmaster/core/test_exp_skills.py -v`

Expected: FAIL because `_init_builtin_tools()` still imports legacy builtin classes, the spawn branch still looks for `"spawn"`, and `_init_skill_tools()` still imports/calls `matmaster.tools.skill_tool.SkillTool`.

- [ ] **Step 4: Implement the registration rewrite**

Update `matmaster/core/exp.py`:

```python
def _init_builtin_tools(
    self,
    ctx: PlaygroundContext,
    registry: ToolRegistry,
    builtin_cfg: list[str],
) -> None:
    allow_all = "*" in builtin_cfg
    allowed: set[str] | None = None if allow_all else set(builtin_cfg)

    def _want(name: str) -> bool:
        return allowed is None or name in allowed

    exec_wd = Path(ctx.execution_workdir)
    registered: list[Any] = []

    if ctx.session is not None:
        from matmaster.tools.builtin import (
            BashTool,
            EditTool,
            GlobTool,
            GrepTool,
            ReadTool,
            WriteTool,
        )

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
                registry.register(tool, source="builtin")
                registered.append(tool)

    from matmaster.tools.builtin import TodoWriteTool, WebFetchTool, WebSearchTool

    sessionless_tools = [
        TodoWriteTool(workdir=ctx.workdir),
        WebSearchTool(),
        WebFetchTool(workdir=ctx.workdir),
    ]
    for tool in sessionless_tools:
        if _want(tool.name):
            registry.register(tool, source="builtin")
            registered.append(tool)

    # MonitorJobTool remains out of scope for the CC rebuild.
    # Do not silently keep the old registration branch here.
```

If you remove `monitor_job` from the builtin runtime, immediately audit the remaining Python references:
- `matmaster/adaptors/calculation/job_service.py`
- `matmaster/integration/bohrium_env.py`
- `matmaster/eval_tooling_snapshot.py`

Update comments, helper text, or fallback guidance there in the same wave so runtime behavior and surrounding documentation do not diverge.

Replace the spawn branch in `build_runtime()`:

```python
if ("Agent" in builtin_cfg or "*" in builtin_cfg) and ctx.session is not None:
    from matmaster.config.loader import list_available_exps
    from matmaster.tools.builtin import AgentTool

    spawn_fn = self._make_spawn_fn(
        ctx,
        source_prefix="MatMaster",
        hook_executor=hook_executor,
    ) if self._allow_spawn else None

    agent_tool = AgentTool(
        session=ctx.session,
        workdir=Path(ctx.execution_workdir),
        spawn_fn=spawn_fn,
        available_exps=list_available_exps(),
    )
    registry.register(agent_tool, source="builtin")
```

Update `_init_skill_tools()`:

```python
from matmaster.tools.builtin.skill_tool import SkillTool
...
skill_tool = SkillTool(
    skill_registry=skill_registry,
    on_skill_hit=on_skill_hit,
)
registry.register(skill_tool, source="skill")
```

Audit `Exp(` constructor call sites while editing:
- The child spawn path must pass `allow_spawn=False`.
- All other existing `Exp(...)` call sites continue to use the default `allow_spawn=True` and do not need churn just to thread the keyword through.

While removing the old task-tool and `monitor_job` registration branches, update every matching assertion in `tests/matmaster/core/test_exp.py` in the same commit so the suite never straddles two incompatible builtin surfaces.

- [ ] **Step 5: Run tests to verify they pass**

Run the same command as Step 2.

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/exp.py \
        tests/matmaster/core/test_exp.py \
        tests/matmaster/core/test_exp_skills.py
git commit -m "refactor(exp): register CC builtin tools and builtin SkillTool"
```

### Task 3: Update downstream Python references and matching regression tests

**Files:**
- Modify: `matmaster/core/capability_policy.py`
- Modify: `matmaster/core/agent.py`
- Modify: `matmaster/tools/tool_compiler.py`
- Modify: `matmaster/eval_tooling_snapshot.py`
- Modify: `matmaster/devshell/runner.py`
- Test: `tests/matmaster/core/test_capability_policy.py`
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`
- Test: `tests/matmaster/tools/test_tool_compiler.py`
- Test: `tests/matmaster/test_eval_tooling_snapshot.py`
- Test: `tests/matmaster/devshell/test_integration.py`

- [ ] **Step 1: Write failing tests for renamed downstream surfaces**

Update these tests:

```python
# tests/matmaster/core/test_capability_policy.py
def test_evaluate_dispatches_bash_tool_by_cc_name(tmp_path: Path) -> None:
    topology = RuntimeTopology(
        session_kind="local",
        control_root="/tmp/ctrl",
        workspace_root="/tmp/ws",
        active_planes=frozenset(ToolPlane),
    )
    session = MagicMock()
    session.exec_bash.return_value = {"stdout": "", "stderr": "", "exit_code": 0}

    instance = ToolCompiler().compile(
        BashTool(session=session, workdir=tmp_path),
        topology,
        source="builtin",
    )

    decision = DefaultCapabilityPolicy().evaluate(
        topology,
        instance,
        {"command": "rm -rf /"},
    )
    assert decision.decision == "deny"
```

```python
# tests/matmaster/tools/test_tool_compiler.py
@pytest.mark.parametrize("tool_name", ["Glob", "Grep"])
def test_local_stateless_relaxes_shell_readers(tool_name: str) -> None:
    ...


@pytest.mark.parametrize("tool_name", ["Glob", "Grep"])
def test_non_local_sessions_do_not_relax(tool_name: str) -> None:
    ...
```

```python
# tests/matmaster/core/test_agent_kernel_stream.py
class SkillStreamProvider:
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(
            tool_call_deltas=[
                {
                    "index": 0,
                    "id": "tc-skill",
                    "name": "Skill",
                    "arguments": '{"skill": "chemistry"}',
                }
            ]
        )
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})
```

```python
# tests/matmaster/test_eval_tooling_snapshot.py
def test_snapshot_default_devshell_skills_disabled() -> None:
    snap = snapshot_devshell_eval_tooling(repo_root=REPO_ROOT)
    assert "Skill" not in snap["tool_names_surface"]
    assert "Bash" in snap["builtin_tool_names"]
    assert "Agent" in snap["builtin_tool_names"]
```

```python
# tests/matmaster/devshell/test_integration.py
yield StreamChunk(
    tool_call_deltas=[
        {
            "index": 0,
            "id": "tc-1",
            "name": "Bash",
            "arguments": '{"command": "echo hello"}',
        }
    ],
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -m pytest tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/test_eval_tooling_snapshot.py tests/matmaster/devshell/test_integration.py -v`

Expected: FAIL because runtime code still keys off `execute_bash`, `use_skill`, lowercase `glob` / `grep`, legacy eval snapshot names, and old devshell hint text.

- [ ] **Step 3: Implement the downstream Python renames**

```python
# matmaster/core/capability_policy.py
if tool_name == "Bash":
    return self.check_bash_safety(tool_args)
```

```python
# matmaster/core/agent.py
if tc.name == "Skill":
    skill_name = tc.arguments.get("skill")
    if isinstance(skill_name, str) and skill_name:
        yield _KernelItem(
            event=SkillHitEvent(
                source="agent",
                skill_name=skill_name,
            )
        )
```

```python
# matmaster/tools/tool_compiler.py
if (
    topology.session_kind == "local"
    and topology.session_capabilities is not None
    and topology.session_capabilities.shell_persistence == "stateless"
    and tool.name in ("Glob", "Grep")
):
    claims = (ResourceClaim(resource="session", mode="shared_read"),)
```

```python
# matmaster/eval_tooling_snapshot.py
_BUILTIN_WHEN_STAR = [
    "Bash", "Read", "Write", "Edit",
    "Glob", "Grep", "TodoWrite",
    "WebSearch", "WebFetch",
]
...
if builtin_cfg == ["*"]:
    return list(_BUILTIN_WHEN_STAR) + ["Agent"]
...
if exp_cfg.skills.enabled:
    surface_tools.append("Skill")
```

```python
# matmaster/devshell/runner.py
hint = (
    "\n\n## Local session\n"
    f"- Workspace directory: `{wd}`\n"
    "- `Bash` uses this directory as cwd; file tools resolve relative paths under it.\n"
    "- **Do not** assume `/share/...` exists here; that path is for **Bohrium remote SSH** "
    "project storage, not for typical local runs.\n"
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run the same command as Step 2.

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/capability_policy.py \
        matmaster/core/agent.py \
        matmaster/tools/tool_compiler.py \
        matmaster/eval_tooling_snapshot.py \
        matmaster/devshell/runner.py \
        tests/matmaster/core/test_capability_policy.py \
        tests/matmaster/core/test_agent_kernel_stream.py \
        tests/matmaster/tools/test_tool_compiler.py \
        tests/matmaster/test_eval_tooling_snapshot.py \
        tests/matmaster/devshell/test_integration.py
git commit -m "refactor: migrate downstream runtime references to CC tool names"
```

## Chunk 2: Configs, Tests, and Docs

### Task 4: Update exp TOML configs and developer instructions

**Files:**
- Modify: `matmaster/exps/direct.toml`
- Modify: `matmaster/exps/explore.toml`

- [ ] **Step 1: Confirm the intended direct-agent builtin surface**

Current state: `matmaster/exps/direct.toml` has `builtin = []`.

Decision gate:
- If spec 6.2 is still correct and the direct agent should expose rebuilt builtin tools, continue to Step 2.
- If direct must remain builtin-free / MCP-only, stop here, update the spec first, and remove Step 2 from this plan before implementation.

- [ ] **Step 2: Replace `direct.toml` builtin list with the CC builtin surface**

```toml
[tools]
builtin = [
    "Bash", "Read", "Write", "Edit",
    "Glob", "Grep", "TodoWrite",
    "Agent", "WebSearch", "WebFetch",
]
mcp = "*"
```

`Skill` stays out of this list because it is registered through `_init_skill_tools()`, not `tools.builtin`.

- [ ] **Step 3: Rewrite `explore.toml` builtin list and developer instructions**

```toml
developer_instructions = '''
You are an exploration sub-agent for Mat Master. Your role is to gather information
and report findings back to the parent agent.

# Scope
- You are a read-only information gatherer. Do NOT create, modify, or delete any files
- Do NOT run commands that change system state (no pip install, no apt-get, no git commit)
- Focus on reading, searching, and understanding the codebase or data

# Tool Usage
- Use Read to examine file contents
- Use Glob to find files by path pattern
- Use Grep to search file contents
- Use Bash only for read-only commands that are not covered by dedicated tools
- Use WebSearch / WebFetch when the answer depends on external web content

# Output
- Be concise and structured. The parent agent will consume your output as a tool result
- Lead with key findings, then supporting details
- Use bullet points or numbered lists for multiple findings
- Include relevant file paths and line numbers when referencing code
- Omit verbose explanations -- the parent agent has its own context

# Constraints
- Stay within the workspace directory
- Do not make assumptions about files you have not read
- If you cannot find the requested information, say so clearly
'''

[tools]
builtin = [
    "Bash",
    "Read",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
]
mcp = ""
```

Keep `Agent` out of `explore.toml`. Explore agents are child research workers and should not recursively spawn more children.

- [ ] **Step 4: Run config validation**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -c "import pathlib, tomllib; direct = tomllib.loads(pathlib.Path('matmaster/exps/direct.toml').read_text()); explore = tomllib.loads(pathlib.Path('matmaster/exps/explore.toml').read_text()); print(direct['tools']['builtin']); print(explore['tools']['builtin'])"`

Expected:
- First line prints the direct builtin list from Step 2 if the decision gate stayed open.
- Second line prints `['Bash', 'Read', 'Glob', 'Grep', 'WebSearch', 'WebFetch']`.

- [ ] **Step 5: Commit**

```bash
git add matmaster/exps/direct.toml matmaster/exps/explore.toml
git commit -m "refactor: update exp configs to CC builtin tool names"
```

### Task 5: Audit and update remaining legacy test references

**Files:**
- Modify: `tests/test_skill_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_skill_tool.py`
- Modify: `tests/matmaster/tools/test_tool_descriptions.py`
- Audit and modify as needed:
  - `tests/test_chat_stream_direct.py`
  - `tests/test_adapt_tool_calls_format.py`
  - `tests/test_stream_replay_skill_hit.py`
  - `tests/matmaster/services/test_agent_run_stream.py`
  - `tests/matmaster/integration/test_quota_pipeline.py`
  - `tests/matmaster/integration/test_sse_skill_hit.py`
  - `tests/matmaster/integration/test_lazy_mcp_integration.py`
  - `tests/matmaster/integration/test_event_payloads.py`
  - `tests/matmaster/core/test_full_tool_runner.py`
  - `tests/matmaster/core/test_structural_validation.py`
  - `tests/matmaster/types/test_events.py`
  - `tests/matmaster/tools/test_skill_tool_callback.py`

- [ ] **Step 1: Run a legacy-name audit over `tests/`**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && rg -n '"execute_bash"|"read_file"|"write_file"|"edit_file"|"list_dir"|"mm_web_search"|"web_fetch"|"use_skill"|"task_create"|"task_get"|"task_list"|"task_update"|"task_complete"|"skill_name"|"spawn"' tests/`

Expected: matches in the files listed above.

Important:
- Rename only real tool IDs, tool-call payload keys, and user-facing prompt text.
- Do **not** rename session API method names such as `session.read_file()` / `session.write_file()`.
- Do **not** rename unrelated fields such as `task_completed`.

- [ ] **Step 2: Rewrite `tests/test_skill_tool.py` to cover the builtin `SkillTool`**

Replace old imports and expectations:

```python
from matmaster.tools.builtin.skill_tool import SkillTool


def test_skill_found(self):
    skill = make_skill()
    tool = SkillTool(skill_registry=make_registry(skill=skill))
    result = asyncio.run(tool.execute({"skill": "test-skill"}))
    assert "Test Skill" in result


def test_slash_prefix_stripped(self):
    skill = make_skill()
    tool = SkillTool(skill_registry=make_registry(skill=skill))
    result = asyncio.run(tool.execute({"skill": "/test-skill"}))
    assert "Test Skill" in result
```

Keep `tests/matmaster/tools/builtin/test_skill_tool.py` aligned with the same payload shape and user-facing behavior. If both files assert the same contract, make the assertions consistent instead of letting the two suites drift.

- [ ] **Step 3: Rewrite `tests/matmaster/tools/test_tool_descriptions.py` to only cover live tools**

Use the rebuilt builtin surface:

```python
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.write_tool import WriteTool

ALL_TOOLS = [BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool]


def test_bash_routes_all_dedicated_tools():
    desc = BashTool().prompt() or ""
    for target in ["Read", "Write", "Edit", "Glob", "Grep"]:
        assert target in desc
```

- [ ] **Step 4: Manually update the remaining audited tests**

Apply the same rules to the remaining files returned by Step 1:
- `execute_bash` -> `Bash`
- `read_file` -> `Read`
- `write_file` -> `Write`
- `edit_file` -> `Edit`
- `list_dir` -> remove or replace with `Glob` only when it is truly a tool ID
- `mm_web_search` -> `WebSearch`
- `web_fetch` -> `WebFetch`
- `use_skill` -> `Skill`
- `skill_name` -> `skill`
- `"spawn"` -> `"Agent"` only when it is the builtin tool name

- [ ] **Step 5: Run targeted test suites**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -m pytest tests/test_skill_tool.py tests/matmaster/tools/test_tool_descriptions.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_structural_validation.py tests/matmaster/integration/test_sse_skill_hit.py tests/matmaster/integration/test_lazy_mcp_integration.py tests/matmaster/integration/test_event_payloads.py tests/matmaster/services/test_agent_run_stream.py tests/test_chat_stream_direct.py tests/test_adapt_tool_calls_format.py -v`

Expected: PASS, or only failures in files that still contain stale audited names from Step 4.

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: migrate legacy builtin tool names in test coverage"
```

### Task 6: Audit model-visible skill docs and prompt markdown for old tool names

**Files:**
- Modify: `matmaster/skills/playground-skills/bohrium-job/SKILL.md`
- Modify: `matmaster/skills/playground-skills/compliance-guardian/SKILL.md`
- Modify: `matmaster/skills/playground-skills/composition-optimization/SKILL.md`
- Modify: `matmaster/skills/playground-skills/deep-survey/SKILL.md`
- Modify: `matmaster/skills/playground-skills/deep-survey/prompts/brief.md`
- Modify: `matmaster/skills/playground-skills/deep-survey/prompts/deep.md`
- Modify: `matmaster/skills/playground-skills/deep-survey/prompts/standard.md`
- Modify: `matmaster/skills/playground-skills/lit-data-organizer/SKILL.md`
- Modify: `matmaster/skills/playground-skills/manuscript-scribe/SKILL.md`
- Modify: `matmaster/skills/playground-skills/manuscript-scribe/prompts/computational_report.md`
- Modify: `matmaster/skills/playground-skills/manuscript-scribe/prompts/patent.md`
- Modify: `matmaster/skills/playground-skills/manuscript-scribe/prompts/research_paper.md`
- Modify: `matmaster/skills/playground-skills/manuscript-scribe/prompts/review.md`
- Modify: `matmaster/skills/playground-skills/manuscript-scribe/prompts/thesis_section.md`
- Modify: `matmaster/skills/playground-skills/result-analysis/SKILL.md`
- Modify: `matmaster/skills/playground-skills/structure-manager/SKILL.md`
- Modify: `matmaster/skills/playground-skills/tasker-polar-surface/SKILL.md`
- Modify: `matmaster/skills/playground-skills/vaspkit-postprocess/SKILL.md`

- [ ] **Step 1: Audit all model-visible skill docs**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && rg -n 'execute_bash|read_file|write_file|edit_file|list_dir|mm_web_search|web_fetch|use_skill|skill_name' matmaster/skills/playground-skills/`

Expected: matches in the files listed above.

- [ ] **Step 2: Update pure tool-name references to the rebuilt surface**

Apply these replacements only when the text is describing an agent-facing tool call:
- `use_skill` -> `Skill`
- `skill_name` -> `skill`
- `execute_bash` -> `Bash`
- `read_file` -> `Read`
- `write_file` -> `Write`
- `edit_file` -> `Edit`
- `mm_web_search` -> `WebSearch`
- `web_fetch` -> `WebFetch`

- [ ] **Step 3: Rewrite prose instead of blindly replacing when examples are no longer literal tool syntax**

Examples:
- If a sentence currently says "Call `use_skill` with ...", rewrite it to "Invoke `Skill` for ..." instead of leaving mixed old/new syntax.
- If a markdown example is explaining a tool payload key, rename `skill_name=` to `skill=` only when it refers to the rebuilt `Skill` tool.
- If a line looks like a mini DSL (for example `use_skill action=run_script ...`), review the whole file manually and rewrite the surrounding explanation; do not treat it as a safe global search/replace.
- Do not rewrite unrelated JSON/result fields such as `task_completed`.

- [ ] **Step 4: Verify the skill-doc audit is clean**

Run the same `rg` command as Step 1.

Expected: no matches in `matmaster/skills/playground-skills/`.

- [ ] **Step 5: Commit**

```bash
git add matmaster/skills/playground-skills
git commit -m "docs(skills): update model-visible tool references to CC naming"
```

### Task 7: Delete the legacy compatibility module

**Files:**
- Delete: `matmaster/tools/skill_tool.py`

- [ ] **Step 1: Verify no runtime or test import still targets the old path**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && rg -n 'from matmaster\.tools\.skill_tool|import matmaster\.tools\.skill_tool' matmaster tests`

Expected: no matches.

- [ ] **Step 2: Delete the legacy file**

```bash
git rm matmaster/tools/skill_tool.py
```

- [ ] **Step 3: Run a quick import check**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -c "from matmaster.tools.builtin import SkillTool; print(SkillTool.name)"`

Expected: `Skill`

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: remove legacy skill tool compatibility module"
```

### Task 8: Final verification

- [ ] **Step 1: Verify the builtin import chain**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -c "from matmaster.tools.builtin import BuiltinTool, BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool, WebSearchTool, WebFetchTool, AgentTool, TodoWriteTool, SkillTool; print([cls.name for cls in [BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool, WebSearchTool, WebFetchTool, AgentTool, TodoWriteTool, SkillTool]])"`

Expected:
`['Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'WebSearch', 'WebFetch', 'Agent', 'TodoWrite', 'Skill']`

- [ ] **Step 2: Verify no legacy runtime names remain in live code/tests/docs**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && rg -n '"execute_bash"|"read_file"|"write_file"|"edit_file"|"list_dir"|"mm_web_search"|"web_fetch"|"use_skill"|"task_create"|"task_get"|"task_list"|"task_update"|"task_complete"|"skill_name"|"spawn"' matmaster tests matmaster/skills/playground-skills/`

Expected: no matches in executable code, tests, or live skill docs. Historical comments may remain only if they are clearly marked as migration notes.

- [ ] **Step 3: Run the focused integration suite**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -m pytest tests/matmaster/core/test_exp.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/tools/test_tool_descriptions.py tests/matmaster/test_eval_tooling_snapshot.py tests/matmaster/devshell/test_integration.py tests/test_skill_tool.py -v`

Expected: all PASS.

- [ ] **Step 4: Run the full test suite**

Run:
`cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run python -m pytest tests/ -q --tb=short`

Expected: all PASS.

- [ ] **Step 5: Final commit if verification fixes were needed**

```bash
git add -A
git commit -m "feat: complete builtin tools integration for CC naming"
```
