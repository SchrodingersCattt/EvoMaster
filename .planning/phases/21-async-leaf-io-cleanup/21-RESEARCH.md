# Phase 21: Async Leaf I/O Cleanup - Research

**Researched:** 2026-03-30
**Domain:** asyncio subprocess / provider API cleanup
**Confidence:** HIGH

## Summary

Phase 21 closes two remaining gaps identified in the v2.0 milestone audit: (1) TOOL-02 -- BashTool native async subprocess, and (2) OpenAIProvider orphaned `chat_with_retry` method. Both are well-scoped leaf-level changes with no cascading architectural impact.

BashTool 当前通过 `BuiltinTool.execute()` 中的 `asyncio.to_thread(self._execute, arguments)` 桥接同步 `session.exec_bash()` 调用。TOOL-02 要求 session-free 执行路径（`matmaster.sessions.local.LocalSession`）改用 `asyncio.create_subprocess_exec` 原生非阻塞 subprocess。session-dependent 路径（evomaster SSH/Docker session）仍保留 `to_thread` 桥接不变。关键设计决策在于 BashTool 需要区分两条路径，使用 native async 的条件判断和 override `execute()` 的方式（类似 SpawnTool 的先例）。

OpenAIProvider 的 `chat_with_retry` 是 Phase 12 从 Protocol 中移除后遗留的孤儿方法，生产代码零调用，仅存在于测试中。直接删除方法 + 清理 `import time` + 删除对应测试类即可。

**Primary recommendation:** BashTool 通过 override `execute()` 实现双路径分发：检测 session 类型为 matmaster LocalSession 时走 native `asyncio.create_subprocess_exec`，否则走 `to_thread` 桥接。OpenAIProvider 直接删除 `chat_with_retry` 方法及其全部测试。

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOOL-02 | BashTool 使用 asyncio.create_subprocess_exec 替代 subprocess.run | BashTool 双路径架构：native async (matmaster LocalSession) + to_thread bridge (evomaster session)。Architecture Patterns 详述实现方案 |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- 始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 按 标准库 > 第三方 > 本地 分组，全部放文件顶部
- 单文件超过 1000 行必须重构
- DAO 层不吞异常；service 层按需降级
- 新增工具必须实现 Tool Protocol 并返回 ToolResult
- pytest-asyncio auto mode 已配置（`asyncio_mode = "auto"`）
- Python 3.10+ (项目实际使用 3.13.2 via uv)

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio (stdlib) | Python 3.13.2 | create_subprocess_exec, wait_for | 标准库，无额外依赖，3.10+ 稳定 API |
| pytest-asyncio | >=0.24.0 | async 测试基础设施 | 项目已配置 auto mode |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| unittest.mock | stdlib | AsyncMock, MagicMock | 测试 mock subprocess 行为 |

No new dependencies required. This phase uses only stdlib `asyncio.subprocess` which is available since Python 3.4 and stable since 3.10+.

## Architecture Patterns

### Current BashTool Execution Flow

```
BashTool._execute(args)                         # sync method
  -> session.exec_bash(command, timeout, ...)    # sync call to session
     -> subprocess.run(...)                      # blocks (inside session)

BuiltinTool.execute(args)                        # async entry point
  -> asyncio.to_thread(self._execute, args)      # bridges sync _execute
```

### Target: Dual-Path BashTool Architecture

```
BashTool.execute(args)                           # override async entry point
  -> if isinstance(session, matmaster.LocalSession):
       -> _execute_async_subprocess(args)         # native asyncio path
            -> asyncio.create_subprocess_exec("bash", "-c", command, ...)
            -> asyncio.wait_for(proc.communicate(), timeout)
  -> else:
       -> asyncio.to_thread(self._execute, args)  # evomaster session bridge
            -> session.exec_bash(...)              # SSH/Docker 同步 API
```

### Pattern 1: Override execute() for Native Async (SpawnTool Precedent)

**What:** SpawnTool already overrides `BuiltinTool.execute()` to bypass `to_thread` and run natively async. BashTool follows the same pattern.

**When to use:** When a tool has a native async code path that should not go through `to_thread`.

**Example:**
```python
# matmaster/tools/builtin/bash_tool.py
import asyncio
from matmaster.sessions.local import LocalSession as MatmasterLocalSession

class BashTool(BuiltinTool):
    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Dual-path: native async for matmaster LocalSession, to_thread for others."""
        if isinstance(self._session, MatmasterLocalSession):
            try:
                return await self._execute_async(arguments)
            except Exception as e:
                self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
                return f"Error: {e}"
        # Fallback: evomaster session via to_thread (existing behavior)
        return await super().execute(arguments)
```

### Pattern 2: asyncio.create_subprocess_exec with Timeout

**What:** Native async subprocess creation + timeout enforcement via `asyncio.wait_for`.

**When to use:** Executing bash commands locally without blocking the event loop.

**Example:**
```python
# Source: https://docs.python.org/3/library/asyncio-subprocess.html
async def _execute_async(self, arguments: dict[str, Any]) -> str:
    command: str = arguments.get("command", "").strip()
    # ... dangerous command check, proxy prefix ...

    timeout_val = arguments.get("timeout", -1)
    timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(self._workdir),
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Command timeout after {timeout}s\n[Command finished with exit code 124]"

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    output = stdout or stderr
    obs = output
    if self._workdir:
        obs += f"\n[Current working directory: {self._workdir}]"
    obs += f"\n[Command finished with exit code {proc.returncode}]"
    return obs
```

### Pattern 3: Clean Removal of Orphaned Method

**What:** Delete `chat_with_retry` from OpenAIProvider + all associated tests.

**When to use:** Method no longer in Protocol, zero production callers.

```python
# openai_provider.py: REMOVE lines 175-247 (chat_with_retry method)
# openai_provider.py: REMOVE `import time` (line 14, only used by chat_with_retry)
# test_openai_provider.py: REMOVE TestChatWithRetry class (lines 490-680)
# test_openai_provider.py: REMOVE test_has_chat_with_retry_method (lines 49-52)
```

### Anti-Patterns to Avoid

- **Modifying matmaster LocalSession to be async:** The Session interface is defined by evomaster `BaseSession` (sync ABC). Do NOT make `LocalSession.exec_bash` async. Instead, BashTool chooses its execution strategy.
- **Creating a new AsyncSession abstraction:** Over-engineering for this phase. TOOL-02 only requires BashTool's session-free path to be native async.
- **Using asyncio.to_thread for the timeout:** The entire point of TOOL-02 is that `create_subprocess_exec` handles timeout natively without thread overhead. Do NOT wrap `asyncio.create_subprocess_exec` in `to_thread`.
- **Breaking session-dependent path:** evomaster SSH/Docker sessions are third-party sync APIs. Do NOT attempt to make them async. `to_thread` is the correct bridge.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async subprocess exec | Custom threading with subprocess.Popen | asyncio.create_subprocess_exec | stdlib, handles event loop integration natively |
| Subprocess timeout | Manual timer thread + process.kill() | asyncio.wait_for(proc.communicate(), timeout) | Race-free, integrates with event loop cancellation |
| Process group cleanup | Manual SIGTERM + sleep + SIGKILL | proc.kill() + await proc.wait() | Sufficient for bash -c invocations; process group management is only needed for persistent daemon processes |

## Common Pitfalls

### Pitfall 1: Forgetting to await proc.wait() after proc.kill()

**What goes wrong:** Zombie processes accumulate if kill() is called without waiting for termination.
**Why it happens:** `proc.kill()` sends SIGKILL but doesn't reap the process.
**How to avoid:** Always `await proc.wait()` after `proc.kill()` (or use `communicate()` which handles this).
**Warning signs:** ResourceWarning about unclosed subprocess in test output.

### Pitfall 2: is_input Mode Not Handled in Async Path

**What goes wrong:** BashTool has an `is_input` parameter for sending input to running processes. The async path must handle this correctly.
**Why it happens:** `is_input=true` is a special session feature (tmux-based on SSH). For matmaster LocalSession, it already returns an error ("not supported").
**How to avoid:** Check `is_input` in the async path and return the same error as the sync LocalSession path. The matmaster `LocalSession.exec_bash` already handles this (returns error dict), so mirroring that behavior in the async path is straightforward.
**Warning signs:** Test for `is_input=true` fails on the async path.

### Pitfall 3: Session Type Detection Ambiguity

**What goes wrong:** Using `isinstance` check against `matmaster.sessions.local.LocalSession` but the Playground creates `evomaster.agent.session.local.LocalSession`. Both are "local" but different classes.
**Why it happens:** There are TWO LocalSession classes in the codebase:
  - `matmaster.sessions.local.LocalSession` -- lightweight, used by DevShell only
  - `evomaster.agent.session.local.LocalSession` -- full evomaster session, used by Playground

**How to avoid:** The isinstance check determines which async strategy to use. Since evomaster `LocalSession` also uses `subprocess.run` internally (via `LocalEnv`), the async path COULD also apply to it. However, evomaster sessions have a richer `exec_bash` interface (PS1 tracking, symlinks, GPU isolation). The safest approach is: check for `matmaster.sessions.local.LocalSession` specifically for the native async path, and treat everything else (including evomaster LocalSession) as session-dependent.

**Alternative:** A more aggressive approach checks whether `session` has a `_workspace_path` attribute (matmaster LocalSession specific). This avoids import coupling. However, isinstance is cleaner and the import is cheap.

**Warning signs:** All production BashTool executions still go through `to_thread` because Playground creates evomaster LocalSession, not matmaster LocalSession.

### Pitfall 4: Removing chat_with_retry Without Checking All References

**What goes wrong:** Stale references in docs/specs cause confusion.
**Why it happens:** `docs/specs/2026-03-24-devshell-implementation.md` mentions `chat_with_retry` 3 times.
**How to avoid:** The docs/specs file is historical documentation, not live code. Updating it is optional but not blocking. Focus on `.py` files only.
**Warning signs:** Grep finds references in markdown files; these are informational, not functional.

## Code Examples

### BashTool Native Async Execute (Full Implementation Pattern)

```python
# Source: asyncio docs + project convention (SpawnTool override pattern)
import asyncio
import sys
from typing import Any, ClassVar

from evomaster.agent.tools.builtin.bash_safety import is_dangerous_bash_command
from matmaster.tools.tool_result import ToolResult
from .base import BuiltinTool

_PROXY_CLEAR_PREFIX = (
    'export http_proxy= https_proxy= ...; '
    'unset http_proxy https_proxy ... 2>/dev/null; '
)

class BashTool(BuiltinTool):
    # ... name, description, json_schema unchanged ...

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Override: native async for matmaster LocalSession, to_thread for others."""
        from matmaster.sessions.local import LocalSession as _MatmasterLocal

        if isinstance(self._session, _MatmasterLocal):
            try:
                return await self._execute_async(arguments)
            except Exception as e:
                self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
                return f"Error: {e}"
        return await super().execute(arguments)

    async def _execute_async(self, arguments: dict[str, Any]) -> str:
        """Native async subprocess path for matmaster LocalSession."""
        command: str = arguments.get("command", "").strip()
        is_input_str: str = arguments.get("is_input", "false")
        is_input = is_input_str == "true"
        timeout_val = arguments.get("timeout", -1)
        timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

        if is_input:
            return "Interactive input is not supported in local session."

        is_dangerous, reason = is_dangerous_bash_command(command)
        if is_dangerous:
            return f"Blocked: {reason}"

        if command and sys.platform != "win32":
            command = _PROXY_CLEAR_PREFIX + command

        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._workdir),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            wd = str(self._workdir) if self._workdir else ""
            obs = f"Command timeout after {timeout}s"
            if wd:
                obs += f"\n[Current working directory: {wd}]"
            obs += "\n[Command finished with exit code 124]"
            return obs

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        output = stdout or stderr
        wd = str(self._workdir) if self._workdir else ""
        obs = output
        if wd:
            obs += f"\n[Current working directory: {wd}]"
        if proc.returncode is not None and proc.returncode != -1:
            obs += f"\n[Command finished with exit code {proc.returncode}]"
        return obs

    def _execute(self, arguments: dict[str, Any]) -> str:
        """Sync path: session-dependent (evomaster SSH/Docker/Local)."""
        # ... existing implementation unchanged ...
```

### OpenAIProvider After Cleanup

```python
# openai_provider.py after removing chat_with_retry:
# - Remove `import time` (line 14)
# - Remove chat_with_retry method (lines 175-247)
# - No other changes needed
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| subprocess.run in BuiltinTool._execute bridged via to_thread | asyncio.create_subprocess_exec for local session | This phase | BashTool no longer occupies a thread from the default executor for local commands |
| chat_with_retry on OpenAIProvider | Retry logic in Kernel._call_llm() | Phase 12/13 (Protocol removed, implementation orphaned) | Clean API surface, no dead code |

## Open Questions

1. **Should evomaster LocalSession also use the native async path?**
   - What we know: evomaster LocalSession also internally uses subprocess (via LocalEnv), but with additional features (symlink management, GPU isolation, PS1 tracking for tmux sessions).
   - What's unclear: Whether bypassing evomaster LocalSession's exec_bash for a direct asyncio subprocess would break any of those features.
   - Recommendation: Keep evomaster LocalSession on the to_thread path. The TOOL-02 requirement explicitly says "session-free" which maps to the matmaster LocalSession. Evomaster sessions are intentionally bridged as third-party sync APIs per project architecture decision.

2. **Behavioral parity: working_dir in output**
   - What we know: Current BashTool._execute reads `working_dir` from session.exec_bash() return dict. The async path uses `self._workdir` directly.
   - What's unclear: Whether `self._workdir` always matches what the session would report.
   - Recommendation: Use `self._workdir` for the async path (it's the execution_workdir injected at Exp.build_runtime). This is the correct value for matmaster LocalSession.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 0.24+ |
| Config file | pyproject.toml (`asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/providers/test_openai_provider.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TOOL-02a | BashTool async path: normal command via create_subprocess_exec | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_normal_command -x` | Wave 0 |
| TOOL-02b | BashTool async path: timeout kills process | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_timeout -x` | Wave 0 |
| TOOL-02c | BashTool async path: dangerous command blocked | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_dangerous_blocked -x` | Wave 0 |
| TOOL-02d | BashTool async path: is_input returns error | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_is_input -x` | Wave 0 |
| TOOL-02e | BashTool session-dependent path unchanged (to_thread) | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolExecution -x` | Existing |
| TOOL-02f | BashTool session=None returns error (no regression) | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolExecution::test_session_not_injected_returns_error -x` | Existing |
| PROV-cleanup | chat_with_retry removed, Protocol conformance maintained | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestProtocolConformance -x` | Existing (needs update) |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/providers/test_openai_provider.py -x`
- **Per wave merge:** `uv run pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess` -- new test class for native async subprocess path (4+ tests)
- [ ] `tests/matmaster/providers/test_openai_provider.py` -- remove `TestChatWithRetry` class + update `test_has_chat_with_retry_method`

## Affected Files Summary

### Must Modify

| File | Change | LOC Estimate |
|------|--------|-------------|
| `matmaster/tools/builtin/bash_tool.py` | Add `execute()` override + `_execute_async()` method | +50 |
| `matmaster/providers/openai_provider.py` | Remove `chat_with_retry` method + `import time` | -75 |
| `tests/matmaster/tools/test_bash_tool.py` | Add `TestBashToolAsyncSubprocess` class | +40 |
| `tests/matmaster/providers/test_openai_provider.py` | Remove `TestChatWithRetry` class + update protocol test | -195 |

### No Change Needed

| File | Reason |
|------|--------|
| `matmaster/tools/builtin/base.py` | BuiltinTool.execute() unchanged; BashTool overrides it |
| `matmaster/sessions/local.py` | Not modified; BashTool bypasses it for async path |
| `matmaster/types/llm_provider.py` | Protocol already correct (no chat_with_retry) |
| `matmaster/core/exp.py` | Tool registration unchanged |

## Sources

### Primary (HIGH confidence)

- Python asyncio subprocess docs: https://docs.python.org/3/library/asyncio-subprocess.html -- create_subprocess_exec API, communicate(), wait_for() timeout pattern
- Project codebase direct inspection: `matmaster/tools/builtin/bash_tool.py`, `matmaster/tools/builtin/base.py`, `matmaster/providers/openai_provider.py`, `matmaster/sessions/local.py`
- Milestone audit: `.planning/v2.0-MILESTONE-AUDIT.md` -- TOOL-02 gap evidence, chat_with_retry orphan identification

### Secondary (MEDIUM confidence)

- SpawnTool execute() override pattern: `matmaster/tools/builtin/spawn_tool.py` -- verified precedent for overriding BuiltinTool.execute()
- Phase 14 success criteria: ROADMAP.md Phase 14 entry -- "BashTool uses asyncio.create_subprocess_exec (session-free) or asyncio.to_thread (session-dependent)"

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - stdlib asyncio, no new dependencies
- Architecture: HIGH - SpawnTool override precedent verified, dual-path design confirmed by codebase inspection
- Pitfalls: HIGH - two LocalSession classes identified and documented, session type detection strategy verified

**Research date:** 2026-03-30
**Valid until:** 2026-04-30 (stable domain, no external dependency changes expected)
