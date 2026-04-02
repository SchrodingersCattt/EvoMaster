# Tool Runtime v2 Parallel Features Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 3 missing tool-runtime-v2 spec features (result trimming, topology-dependent binding, input_validator) on a parallel branch isolated from Phase 34-36.

**Architecture:** Three sequential tasks (Chunk 1 → 2 → 3) targeting the tool subsystem only (tool_spec.py, tool_runner.py, tool_compiler.py, builtin/*.py). Zero file overlap with Phase 34-36 which modifies agent.py/exp.py/hooks.py. Each task produces a self-contained commit. Chunk 2 and 3 build on Chunk 1's BUILTIN_META 4-tuple format.

**Tech Stack:** Python 3.10+, Pydantic frozen models, pytest + pytest-asyncio, existing ToolRunner/ToolCompiler/ToolCatalog infrastructure.

**Spec:** `docs/superpowers/specs/2026-04-02-tool-runtime-v2-parallel-features-design.md`

---

## Chunk 1: ToolSpec + Result Trimming

### Task 1: ToolSpec fields + BUILTIN_META extension + normalize + truncation

**Files:**
- Modify: `matmaster/types/tool_spec.py:30-47` (add fields to ToolSpec)
- Modify: `matmaster/tools/tool_compiler.py:31-48,68-71` (extend BUILTIN_META + unpack)
- Modify: `matmaster/core/tool_runner.py:311-322` (add normalize + truncation after executor)
- Test: `tests/matmaster/types/test_tool_spec.py` (extend)
- Test: `tests/matmaster/core/test_full_tool_runner.py` (extend)
- Test: `tests/matmaster/tools/test_tool_compiler.py` (extend)

- [ ] **Step 1: Write failing tests for ToolSpec new fields**

In `tests/matmaster/types/test_tool_spec.py`, add:

```python
class TestToolSpecNewFields:
    def test_max_result_chars_default_zero(self) -> None:
        spec = ToolSpec(tool_name="test")
        assert spec.max_result_chars == 0

    def test_max_result_chars_set(self) -> None:
        spec = ToolSpec(tool_name="test", max_result_chars=12000)
        assert spec.max_result_chars == 12000

    def test_usage_hint_default_empty(self) -> None:
        spec = ToolSpec(tool_name="test")
        assert spec.usage_hint == ""

    def test_usage_hint_set(self) -> None:
        spec = ToolSpec(tool_name="test", usage_hint="Use for reading files")
        assert spec.usage_hint == "Use for reading files"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/types/test_tool_spec.py::TestToolSpecNewFields -v`
Expected: FAIL — `max_result_chars` and `usage_hint` not recognized by ToolSpec.

- [ ] **Step 3: Add max_result_chars and usage_hint to ToolSpec**

In `matmaster/types/tool_spec.py`, add two fields after `fast_path_eligible` (line 46):

```python
    fast_path_eligible: bool = False
    max_result_chars: int = 0
    usage_hint: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/types/test_tool_spec.py::TestToolSpecNewFields -v`
Expected: PASS

- [ ] **Step 5: Write failing test for BUILTIN_META max_result_chars propagation**

In `tests/matmaster/tools/test_tool_compiler.py`, add:

```python
class TestToolCompilerMaxResultChars:
    def test_read_file_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("read_file"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 12000

    def test_execute_bash_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("execute_bash"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 12000

    def test_glob_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("glob"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 8000

    def test_web_fetch_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("web_fetch"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 16000

    def test_unknown_tool_max_result_chars_zero(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("custom"), _make_topology(), source="mcp")
        assert instance.tool_spec.max_result_chars == 0
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_tool_compiler.py::TestToolCompilerMaxResultChars -v`
Expected: FAIL — ToolSpec doesn't receive max_result_chars from BUILTIN_META.

- [ ] **Step 7: Extend BUILTIN_META to 4-tuple and update compile()**

In `matmaster/tools/tool_compiler.py`:

Replace BUILTIN_META type and values (lines 31-48):

```python
BUILTIN_META: dict[str, tuple[ToolPlane, str, bool, int]] = {
    "execute_bash": (ToolPlane.SESSION_SHELL, "local_mutation", False, 12000),
    "list_dir": (ToolPlane.SESSION_SHELL, "pure_read", False, 8000),
    "glob": (ToolPlane.SESSION_SHELL, "pure_read", False, 8000),
    "grep": (ToolPlane.SESSION_SHELL, "pure_read", False, 8000),
    "read_file": (ToolPlane.SESSION_FS, "pure_read", True, 12000),
    "write_file": (ToolPlane.SESSION_FS, "local_mutation", False, 0),
    "edit_file": (ToolPlane.SESSION_FS, "local_mutation", False, 0),
    "task_create": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "task_get": (ToolPlane.CONTROL_PLANE, "pure_read", True, 0),
    "task_list": (ToolPlane.CONTROL_PLANE, "pure_read", True, 0),
    "task_update": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "task_complete": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "mm_web_search": (ToolPlane.EXTERNAL_SERVICE, "external_write", False, 0),
    "web_fetch": (ToolPlane.EXTERNAL_SERVICE, "external_write", False, 16000),
    "spawn": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "monitor_job": (ToolPlane.SESSION_FS, "external_write", False, 0),
}
```

Update compile() unpack (line 68-71):

```python
        plane, effect_level, fast_path, max_result_chars = BUILTIN_META.get(
            tool.name,
            (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
        )
```

Add `max_result_chars` to ToolSpec construction (after line 78):

```python
        spec = ToolSpec(
            tool_name=tool.name,
            description=tool.description,
            args_schema=tool.json_schema,
            source=source,
            effect_level=effect_level,
            fast_path_eligible=fast_path,
            max_result_chars=max_result_chars,
        )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_tool_compiler.py -v`
Expected: ALL PASS (new + existing)

- [ ] **Step 9: Write failing tests for normalize + truncation in FullToolRunner**

In `tests/matmaster/core/test_full_tool_runner.py`, add import at top (Path may already be imported via tmp_path fixture, but explicit is fine):

```python
from pathlib import Path
```

Add a helper tool that returns a raw string (not ToolResult):

```python
class _StringReturnTool:
    """Tool that returns a plain string (not ToolResult)."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"string tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        return self._result
```

Add test class:

```python
class TestNormalizeAndTruncation:
    @pytest.mark.asyncio
    async def test_string_return_is_normalized(self) -> None:
        """Executor returning str is normalized to ToolResult."""
        registry = ToolRegistry()
        registry.register(_StringReturnTool("read_file", result="some content"), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file")], ctx)
        _, tr = results[0]
        assert isinstance(tr, ToolResult)
        assert tr.content == "some content"
        assert tr.status == "success"

    @pytest.mark.asyncio
    async def test_none_return_is_normalized(self) -> None:
        """Executor returning None is normalized to empty ToolResult."""

        class _NoneReturnTool:
            name = "read_file"
            description = "none tool"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}

            async def execute(self, arguments: dict[str, Any]) -> None:
                return None

        registry = ToolRegistry()
        registry.register(_NoneReturnTool(), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file")], ctx)
        _, tr = results[0]
        assert isinstance(tr, ToolResult)
        assert tr.content == ""

    @pytest.mark.asyncio
    async def test_truncation_triggers_on_oversized_content(self, tmp_path: Path) -> None:
        """Content exceeding max_result_chars is truncated."""
        long_content = "A" * 20000
        registry = ToolRegistry()
        registry.register(_StringReturnTool("read_file", result=long_content), source="builtin")
        topology = _make_topology()
        topology_with_tmp = RuntimeTopology(
            session_kind="local",
            control_root=str(tmp_path),
            workspace_root="/tmp/ws",
            active_planes=frozenset(ToolPlane),
        )
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog, topology=topology_with_tmp)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file", call_id="call_123")], ctx)
        _, tr = results[0]

        assert len(tr.content) < 20000
        assert "truncated" in tr.content
        assert tr.meta.get("truncated") is True
        assert "full_result_path" in tr.meta

        # Verify disk file
        disk_path = Path(tr.meta["full_result_path"])
        assert disk_path.exists()
        assert disk_path.read_text() == long_content

    @pytest.mark.asyncio
    async def test_no_truncation_when_under_limit(self) -> None:
        """Content under max_result_chars is not truncated."""
        short_content = "short"
        registry = ToolRegistry()
        registry.register(_StringReturnTool("read_file", result=short_content), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file")], ctx)
        _, tr = results[0]

        assert tr.content == short_content
        assert "truncated" not in tr.meta

    @pytest.mark.asyncio
    async def test_no_truncation_when_max_result_chars_zero(self) -> None:
        """Tools with max_result_chars=0 are never truncated."""
        long_content = "B" * 100000
        registry = ToolRegistry()
        registry.register(_StringReturnTool("write_file", result=long_content), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.content == long_content
        assert "truncated" not in tr.meta
```

- [ ] **Step 10: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_full_tool_runner.py::TestNormalizeAndTruncation -v`
Expected: FAIL — no normalize or truncation logic in FullToolRunner.

- [ ] **Step 11: Implement normalize + truncation in FullToolRunner**

In `matmaster/core/tool_runner.py`, add import at top (after existing imports, ~line 35):

```python
from matmaster.tools.tool_result import normalize_tool_result
```

Add `_truncate_result` method to FullToolRunner class (after `__init__`, before `execute_batch`):

```python
    def _truncate_result(
        self, tr: ToolResult, max_chars: int, tool_call_id: str
    ) -> ToolResult:
        """Truncate oversized content, save full result to disk."""
        from pathlib import Path

        # Save full content to control_root (always local)
        results_dir = Path(self._topology.control_root) / ".tool_results"
        results_dir.mkdir(parents=True, exist_ok=True)
        full_path = results_dir / f"{tool_call_id}.txt"
        full_path.write_text(tr.content, encoding="utf-8")

        # Truncate
        tail_len = min(2000, max_chars // 4)
        head = tr.content[: max_chars // 2]
        tail = tr.content[-tail_len:] if tail_len > 0 else ""
        truncated_chars = len(tr.content) - len(head) - len(tail)
        notice = (
            f"\n\n... [{truncated_chars} chars truncated; "
            f"re-run with more specific parameters to see full output] ...\n\n"
        )
        truncated_content = head + notice + tail

        new_meta = {**tr.meta, "full_result_path": str(full_path), "truncated": True}
        return ToolResult(
            status=tr.status,
            content=truncated_content,
            payload=tr.payload,
            meta=new_meta,
        )
```

In `execute_batch`, replace the execute block (lines 311-322) with:

```python
            # 8. Execute + Release
            try:
                tr = await instance.tool_executor(tc.arguments)
            except Exception as e:
                tr = ToolResult.from_error(tc.name, e)
            finally:
                if ticket is not None:
                    await self._scheduler.release(ticket)

            # 9a. Normalize (builtins may return str or None)
            tr = normalize_tool_result(tr)

            # 9b. Truncate oversized content
            max_chars = instance.tool_spec.max_result_chars
            if max_chars > 0 and len(tr.content) > max_chars:
                tr = self._truncate_result(tr, max_chars, tc.id)

            results.append((tc, tr))
            if on_result:
                await on_result(tc, tr)
```

- [ ] **Step 12: Run all tests to verify**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_full_tool_runner.py tests/matmaster/types/test_tool_spec.py tests/matmaster/tools/test_tool_compiler.py -v`
Expected: ALL PASS

- [ ] **Step 13: Run full test suite for regressions**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/ -x -q`
Expected: ALL PASS, no regressions.

- [ ] **Step 14: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add matmaster/types/tool_spec.py matmaster/tools/tool_compiler.py matmaster/core/tool_runner.py tests/matmaster/types/test_tool_spec.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/core/test_full_tool_runner.py
git commit -m "feat: ToolSpec max_result_chars + FullToolRunner normalize/truncation

- Add max_result_chars and usage_hint fields to ToolSpec
- Extend BUILTIN_META to 4-tuple with per-tool max_result_chars values
- Add normalize_tool_result() in FullToolRunner after executor returns
- Add _truncate_result() writing full content to control_root/.tool_results/
- Truncation uses head + notice + scaled tail, sets meta truncated/full_result_path"
```

---

## Chunk 2: Topology-Dependent Binding + fast_path_eligible Fix

### Task 2: ToolCompiler topology-dependent claims + fast_path_eligible correction

**Files:**
- Modify: `matmaster/tools/tool_compiler.py:31-48,66-84` (BUILTIN_META fix + compile topology logic)
- Test: `tests/matmaster/tools/test_tool_compiler.py` (extend)

- [ ] **Step 1: Write failing tests for topology-dependent binding**

In `tests/matmaster/tools/test_tool_compiler.py`, add import:

```python
from matmaster.types.topology import SessionCapabilities
```

Add helper:

```python
def _make_local_stateless_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
        session_capabilities=SessionCapabilities(
            shell_persistence="stateless",
            file_ops="native",
        ),
    )

def _make_ssh_stateless_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="ssh",
        control_root="/tmp/control",
        workspace_root="/remote/workspace",
        active_planes=frozenset(ToolPlane),
        session_capabilities=SessionCapabilities(
            shell_persistence="stateless",
            file_ops="sftp",
        ),
    )
```

Add test class:

```python
class TestTopologyDependentBinding:
    def test_glob_local_stateless_shared_read(self) -> None:
        """Local + stateless -> glob gets shared_read claim."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("glob"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="shared_read"),
        )

    def test_grep_local_stateless_shared_read(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("grep"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="shared_read"),
        )

    def test_list_dir_local_stateless_shared_read(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("list_dir"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="shared_read"),
        )

    def test_glob_ssh_stays_exclusive(self) -> None:
        """SSH session -> glob stays exclusive even if stateless."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("glob"), _make_ssh_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="exclusive"),
        )

    def test_glob_local_no_caps_stays_exclusive(self) -> None:
        """Local but session_capabilities=None -> no relaxation."""
        compiler = ToolCompiler()
        topo = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/c",
            workspace_root="/tmp/w",
        )
        instance = compiler.compile(_FakeTool("glob"), topo, source="builtin")
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="exclusive"),
        )

    def test_bash_local_stays_exclusive(self) -> None:
        """execute_bash is never relaxed."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("execute_bash"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="exclusive"),
        )

    def test_custom_tool_unaffected(self) -> None:
        """Non-builtin tools are not affected by relaxation."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("my_mcp_tool"), _make_local_stateless_topology(), source="mcp"
        )
        assert instance.tool_binding.resource_claims == ()


class TestFastPathEligibleFix:
    def test_glob_fast_path_eligible(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("glob"), _make_topology(), source="builtin")
        assert instance.tool_spec.fast_path_eligible is True

    def test_grep_fast_path_eligible(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("grep"), _make_topology(), source="builtin")
        assert instance.tool_spec.fast_path_eligible is True

    def test_list_dir_fast_path_eligible(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("list_dir"), _make_topology(), source="builtin")
        assert instance.tool_spec.fast_path_eligible is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_tool_compiler.py::TestTopologyDependentBinding tests/matmaster/tools/test_tool_compiler.py::TestFastPathEligibleFix -v`
Expected: FAIL — glob/grep/list_dir have fast_path_eligible=False and claims are always exclusive.

- [ ] **Step 3: Fix fast_path_eligible and add topology relaxation in compile()**

In `matmaster/tools/tool_compiler.py`:

Fix BUILTIN_META (lines 33-35) — change False to True for list_dir/glob/grep (Chunk 1 already extended these to 4-tuples):

```python
    "list_dir": (ToolPlane.SESSION_SHELL, "pure_read", True, 8000),
    "glob": (ToolPlane.SESSION_SHELL, "pure_read", True, 8000),
    "grep": (ToolPlane.SESSION_SHELL, "pure_read", True, 8000),
```

In compile(), replace `_ = topology` (line 66) and add topology-dependent logic after claims lookup:

```python
        claims = BUILTIN_CLAIMS.get(tool.name, ())

        # Topology-dependent binding relaxation (spec 8.2)
        if (
            topology.session_kind == "local"
            and topology.session_capabilities is not None
            and topology.session_capabilities.shell_persistence == "stateless"
            and tool.name in ("list_dir", "glob", "grep")
        ):
            claims = (ResourceClaim(resource_id="session", mode="shared_read"),)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_tool_compiler.py -v`
Expected: ALL PASS (new + existing)

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/ -x -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add matmaster/tools/tool_compiler.py tests/matmaster/tools/test_tool_compiler.py
git commit -m "feat: ToolCompiler topology-dependent binding + fast_path_eligible fix

- Fix fast_path_eligible for glob/grep/list_dir: False -> True
- compile() consumes RuntimeTopology to relax claims for local+stateless
- list_dir/glob/grep get shared_read in local session (subprocess isolation)
- SSH stays exclusive (channel reuse). No caps -> no relaxation.
- Metadata preparation for future execute_batch concurrency"
```

---

## Chunk 3: input_validator System

### Task 3: ToolInstance.input_validator + BuiltinTool hook + WriteTool/EditTool + FullToolRunner Step 3

**Files:**
- Modify: `matmaster/types/tool_spec.py:83-93` (add input_validator to ToolInstance)
- Modify: `matmaster/tools/builtin/base.py:50-56` (add validate_input hook)
- Modify: `matmaster/tools/builtin/write_tool.py` (add validate_input)
- Modify: `matmaster/tools/builtin/edit_tool.py:86-104` (add validate_input)
- Modify: `matmaster/tools/tool_compiler.py:85-89` (bind input_validator)
- Modify: `matmaster/core/tool_runner.py:258-260` (add Step 3 in execute_batch)
- Test: `tests/matmaster/tools/test_builtin_validators.py` (create)
- Test: `tests/matmaster/core/test_full_tool_runner.py` (extend)
- Test: `tests/matmaster/tools/test_tool_compiler.py` (extend)

- [ ] **Step 1: Write failing tests for WriteTool.validate_input and EditTool.validate_input**

Create `tests/matmaster/tools/test_builtin_validators.py`:

```python
"""Tests for builtin tool input validators (WriteTool, EditTool)."""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.types.tool_decision import ToolDecision


class TestWriteToolValidator:
    @pytest.mark.asyncio
    async def test_deny_empty_path(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "", "content": "x"})
        assert result is not None
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_deny_path_outside_workdir(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/etc/passwd", "content": "x"})
        assert result is not None
        assert result.decision == "deny"
        assert "outside workspace" in result.reason

    @pytest.mark.asyncio
    async def test_deny_same_prefix_different_dir(self) -> None:
        """'/workspace_evil/f.txt' must NOT pass for workdir='/workspace'."""
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/workspace_evil/f.txt", "content": "x"})
        assert result is not None
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_allow_path_inside_workdir(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/workspace/src/main.py", "content": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_deny_when_no_workdir(self) -> None:
        """Fail closed: workdir=None -> deny (safety boundary)."""
        tool = WriteTool()
        result = await tool.validate_input({"file_path": "/anywhere/file.txt", "content": "x"})
        assert result is not None
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_deny_traversal_escaping_workdir(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/workspace/../etc/passwd", "content": "x"})
        assert result is not None
        assert result.decision == "deny"


class TestEditToolValidator:
    @pytest.mark.asyncio
    async def test_deny_empty_old_str(self) -> None:
        tool = EditTool()
        result = await tool.validate_input(
            {"file_path": "f.py", "old_str": "", "new_str": "x"}
        )
        assert result is not None
        assert result.decision == "deny"
        assert "empty" in result.reason

    @pytest.mark.asyncio
    async def test_deny_identical_strings(self) -> None:
        tool = EditTool()
        result = await tool.validate_input(
            {"file_path": "f.py", "old_str": "same", "new_str": "same"}
        )
        assert result is not None
        assert result.decision == "deny"
        assert "identical" in result.reason

    @pytest.mark.asyncio
    async def test_allow_valid_edit(self) -> None:
        tool = EditTool()
        result = await tool.validate_input(
            {"file_path": "f.py", "old_str": "old", "new_str": "new"}
        )
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_builtin_validators.py -v`
Expected: FAIL — `validate_input` not defined on WriteTool/EditTool.

- [ ] **Step 3: Add validate_input to BuiltinTool base class**

In `matmaster/tools/builtin/base.py`, add import at top:

```python
from matmaster.types.tool_decision import ToolDecision
```

Add method after `execute()` (after line 56):

```python
    async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
        """Tool-specific semantic input validation.

        Override to reject invalid arguments before execution.
        Return None to allow, ToolDecision(decision='deny', ...) to reject.
        """
        return None
```

- [ ] **Step 4: Implement WriteTool.validate_input**

In `matmaster/tools/builtin/write_tool.py`, add import at top:

```python
from matmaster.types.tool_decision import ToolDecision
```

Add method after `__init__` (after line 53), before `_execute`:

```python
    async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
        from pathlib import PurePosixPath

        file_path = arguments.get("file_path", "")
        if not file_path:
            return ToolDecision(decision="deny", reason="file_path is required")
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set, cannot validate path")
        # Parent-child containment via PurePosixPath.is_relative_to (not string prefix)
        try:
            resolved = PurePosixPath(posixpath.normpath(file_path))
            if not resolved.is_relative_to(self._workdir):
                return ToolDecision(
                    decision="deny",
                    reason=f"file_path '{file_path}' is outside workspace boundary",
                )
        except (TypeError, ValueError):
            return ToolDecision(decision="deny", reason=f"invalid file_path: '{file_path}'")
        return None
```

- [ ] **Step 5: Implement EditTool.validate_input**

In `matmaster/tools/builtin/edit_tool.py`, add import at top:

```python
from matmaster.types.tool_decision import ToolDecision
```

Add method after `__init__` (after line 84), before `_execute`:

```python
    async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
        old_str = arguments.get("old_str", "")
        new_str = arguments.get("new_str", "")
        if not old_str:
            return ToolDecision(decision="deny", reason="old_str must not be empty")
        if old_str == new_str:
            return ToolDecision(
                decision="deny",
                reason="old_str and new_str are identical, no edit needed",
            )
        return None
```

- [ ] **Step 6: Run validator tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_builtin_validators.py -v`
Expected: PASS

- [ ] **Step 7: Write failing tests for ToolInstance.input_validator + ToolCompiler binding**

In `tests/matmaster/tools/test_tool_compiler.py`, add:

```python
class TestToolCompilerInputValidator:
    def test_tool_with_validate_input_gets_bound(self) -> None:
        """Tools with validate_input get input_validator on ToolInstance."""

        class _ValidatableTool:
            name = "write_file"
            description = "validatable"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}

            async def execute(self, arguments: dict[str, Any]) -> ToolResult:
                return ToolResult(content="ok")

            async def validate_input(self, arguments: dict[str, Any]):
                return None

        compiler = ToolCompiler()
        instance = compiler.compile(
            _ValidatableTool(), _make_topology(), source="builtin"
        )
        assert instance.input_validator is not None

    def test_tool_without_validate_input_gets_none(self) -> None:
        """Regular tools get input_validator=None."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("read_file"), _make_topology(), source="builtin"
        )
        assert instance.input_validator is None
```

- [ ] **Step 8: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_tool_compiler.py::TestToolCompilerInputValidator -v`
Expected: FAIL — ToolInstance has no `input_validator` field.

- [ ] **Step 9: Add input_validator to ToolInstance**

In `matmaster/types/tool_spec.py`, add import at top (line 21, extend existing):

```python
from matmaster.types.tool_decision import ToolDecision
```

Extend ToolInstance (after line 93):

```python
@dataclass(frozen=True)
class ToolInstance:
    """Frozen unit combining spec + binding + executor.

    This is what ToolCatalog stores and ToolRunner consumes.

    NOTE: tool_executor annotation says Awaitable[ToolResult] but builtins
    may return str | ToolResult | None at runtime. FullToolRunner calls
    normalize_tool_result() after execution to handle this. The annotation
    is historical type debt — fixing it to Awaitable[str | ToolResult | None]
    is deferred to avoid changing the executor contract in this plan.
    """

    tool_spec: ToolSpec
    tool_binding: ToolBinding
    tool_executor: Callable[[dict[str, Any]], Awaitable[ToolResult]]
    input_validator: Callable[[dict[str, Any]], Awaitable[ToolDecision | None]] | None = None
```

- [ ] **Step 10: Bind input_validator in ToolCompiler**

In `matmaster/tools/tool_compiler.py`, update the return statement in compile() to detect and bind validate_input:

```python
        validator = None
        if hasattr(tool, "validate_input") and callable(tool.validate_input):
            validator = tool.validate_input

        return ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=tool.execute,
            input_validator=validator,
        )
```

- [ ] **Step 11: Run ToolCompiler validator tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_tool_compiler.py -v`
Expected: ALL PASS

- [ ] **Step 12: Write failing tests for FullToolRunner Step 3 (input_validator)**

In `tests/matmaster/core/test_full_tool_runner.py`, add:

```python
class TestInputValidatorInRunner:
    @pytest.mark.asyncio
    async def test_deny_validator_returns_error(self) -> None:
        """input_validator deny -> error ToolResult, executor not called."""
        registry = ToolRegistry()
        tool = _SimpleTool("write_file", result="should not reach")
        registry.register(tool, source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        # Inject a validator that denies
        instance = catalog.get_tool("write_file")
        assert instance is not None

        # Patch the catalog to return a ToolInstance with a deny validator.
        async def _deny_validator(args: dict[str, Any]) -> ToolDecision:
            return ToolDecision(decision="deny", reason="path outside boundary")

        executor_called = False
        original_executor = instance.tool_executor

        async def _tracking_executor(args: dict[str, Any]) -> ToolResult:
            nonlocal executor_called
            executor_called = True
            return await original_executor(args)

        patched = ToolInstance(
            tool_spec=instance.tool_spec,
            tool_binding=instance.tool_binding,
            tool_executor=_tracking_executor,
            input_validator=_deny_validator,
        )

        # Patch catalog to return our custom instance
        original_get = catalog.get_tool
        catalog.get_tool = lambda name: patched if name == "write_file" else original_get(name)

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.status == "error"
        assert "path outside boundary" in tr.content
        assert tr.meta.get("layer") == "input_validation"
        assert not executor_called

    @pytest.mark.asyncio
    async def test_validator_exception_returns_error(self) -> None:
        """input_validator raising exception -> error ToolResult."""
        registry = ToolRegistry()
        tool = _SimpleTool("write_file", result="should not reach")
        registry.register(tool, source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        async def _exploding_validator(args: dict[str, Any]) -> None:
            raise ValueError("validator kaboom")

        instance = catalog.get_tool("write_file")
        patched = ToolInstance(
            tool_spec=instance.tool_spec,
            tool_binding=instance.tool_binding,
            tool_executor=instance.tool_executor,
            input_validator=_exploding_validator,
        )
        catalog.get_tool = lambda name: patched if name == "write_file" else None

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.status == "error"
        assert "validator kaboom" in tr.content
        assert tr.meta.get("layer") == "input_validation"

    @pytest.mark.asyncio
    async def test_allow_validator_lets_execution_proceed(self) -> None:
        """input_validator returning None -> execution proceeds normally."""
        registry = ToolRegistry()
        tool = _SimpleTool("write_file", result="written ok")
        registry.register(tool, source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        async def _allow_validator(args: dict[str, Any]) -> None:
            return None

        instance = catalog.get_tool("write_file")
        patched = ToolInstance(
            tool_spec=instance.tool_spec,
            tool_binding=instance.tool_binding,
            tool_executor=instance.tool_executor,
            input_validator=_allow_validator,
        )
        catalog.get_tool = lambda name: patched if name == "write_file" else None

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.status == "success"
        assert tr.content == "written ok"
```

- [ ] **Step 13: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_full_tool_runner.py::TestInputValidatorInRunner -v`
Expected: FAIL — FullToolRunner doesn't check input_validator.

- [ ] **Step 14: Add input_validator Step 3 to FullToolRunner.execute_batch**

In `matmaster/core/tool_runner.py`, in execute_batch, add after StructuralValidation block (after the `continue` at ~line 258) and before RunStateGuard block (~line 260):

```python
            # 3b. input_validator (tool-specific semantic check)
            if instance.input_validator is not None:
                try:
                    iv_decision = await instance.input_validator(tc.arguments)
                except Exception as exc:
                    tr = ToolResult(
                        status="error",
                        content=str(exc),
                        meta={"layer": "input_validation"},
                    )
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue
                if iv_decision is not None and iv_decision.decision == "deny":
                    tr = ToolResult(
                        status="error",
                        content=iv_decision.reason,
                        meta={"layer": "input_validation"},
                    )
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue
```

- [ ] **Step 15: Run all tests to verify**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_full_tool_runner.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/tools/test_builtin_validators.py -v`
Expected: ALL PASS

- [ ] **Step 16: Run full test suite for regressions**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/ -x -q`
Expected: ALL PASS

- [ ] **Step 17: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add matmaster/types/tool_spec.py matmaster/tools/builtin/base.py matmaster/tools/builtin/write_tool.py matmaster/tools/builtin/edit_tool.py matmaster/tools/tool_compiler.py matmaster/core/tool_runner.py tests/matmaster/tools/test_builtin_validators.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/tools/test_tool_compiler.py
git commit -m "feat: input_validator system for tool-specific semantic validation

- Add input_validator field to ToolInstance (optional Callable)
- Add validate_input() hook to BuiltinTool base class
- WriteTool: validates file_path within workspace boundary (pure semantic)
- EditTool: validates old_str non-empty + old_str != new_str
- ToolCompiler: binds validate_input if present on tool
- FullToolRunner: Step 3 input_validator check with exception handling
- read-before-modify stays in _execute (runtime state, Phase 35 scope)"
```
