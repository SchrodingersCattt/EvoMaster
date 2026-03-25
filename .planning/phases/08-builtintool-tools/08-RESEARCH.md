# Phase 8: BuiltinTool 基础设施与核心 Tools - Research

**Researched:** 2026-03-25
**Domain:** Python ABC 设计 / 远程 session 命令执行 / 工具注册体系
**Confidence:** HIGH

## Summary

Phase 8 需要建立 matmaster 原生 BuiltinTool 基类体系，并交付 BashTool、ListDirTool 和 5 个 TaskTool（TaskCreate/TaskGet/TaskList/TaskUpdate/TaskComplete）。核心设计模式已在 CONTEXT.md 中锁定：构造注入 session/workdir，Tool Protocol 签名 `execute(arguments: dict) -> str` 保持不变，Kernel 不感知 session 概念。

项目已有完整的参考实现：`evomaster/agent/tools/builtin/bash.py` 提供了 BashTool 的远程执行逻辑参考，`EvoToolAdapter` 验证了构造注入模式的可行性，`tool_registry.py` 定义了 Tool Protocol 和 ToolRegistry。Phase 8 的工作本质是：(1) 抽取一个 BuiltinTool ABC 消除样板代码，(2) 将 evomaster BashTool 的核心逻辑迁移为原生实现，(3) 新建 ListDirTool 和 TaskTool 套件，(4) 改造 `Exp._init_builtin_tools()` 支持双源注册。

**Primary recommendation:** BuiltinTool 基类用普通 class + ABC，不用 dataclass。构造参数为 session(Any|None) 和 workdir(Path|None)。子类只需定义 name/description/json_schema 类属性并实现 `_execute(arguments) -> str` 模板方法。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: 构造注入模式。Tool Protocol execute(arguments: dict[str, Any]) -> str 签名不变。session/workdir 在 Exp assemble 阶段通过 BuiltinTool 构造函数注入。Kernel 和 ToolRegistry 不感知 session。
- D-02: 不引入 ToolContext 参数类型。此决策解除 STATE.md 标记的 ToolContext blocker。
- D-03: 统一基类设计。BuiltinTool 抽象基类，session/workdir 构造注入（可选参数）。Phase 8/9/11 的 builtin tool 全部继承此基类。
- D-04: BuiltinTool 基类满足 Tool Protocol（name/description/json_schema/execute），子类只需实现具体逻辑。
- D-05: 5 tool 分离设计对齐 Claude Code：TaskCreate/TaskGet/TaskList/TaskUpdate/TaskComplete。
- D-06: Task 状态持久化到 workdir/.tasks.json。跨 run 持久，文件可审计。
- D-07: TaskComplete 与 TaskUpdate 语义分离。
- D-08: Phase 8-9 过渡期保持 ExpConfig.tools.builtin = ["*"]。native BuiltinTool 先注册，MonitorJobTool 继续走 EvoToolAdapter。
- D-09: source 标签区分：native tool 用 "builtin"，evo adapter tool 用 "builtin_evo"。

### Claude's Discretion
- BuiltinTool 基类的具体字段设计（哪些构造参数、是否用 dataclass 还是普通 class）
- TaskTool 的 tasks.json 文件格式和 schema 细节
- BashTool/ListDirTool 的具体实现方式（通过 session 执行远程命令的机制）
- _init_builtin_tools 内部新旧 tool 注册的具体代码组织

### Deferred Ideas (OUT OF SCOPE)
- ExpConfig.tools.builtin 从 wildcard 切换到显式列举 -- Phase 9
- 清除 EvoToolAdapter 对 BashTool/EditorTool 的依赖 -- Phase 9
- MonitorJobTool 原生化 -- 未定
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOOL-04 | Agent 可以通过 Bash tool 在远程环境执行 shell 命令 | BuiltinTool 基类 + 原生 BashTool 实现，复用 evomaster bash_safety + session.exec_bash |
| TOOL-07 | Agent 可以通过 ListDir tool 列出远程目录结构 | ListDirTool 通过 session.exec_bash 执行 ls 命令，格式化输出 |
| TOOL-09 | Agent 可以通过 Task 套件创建、更新、查询任务状态 | 5 个 TaskTool（session-free），持久化到 workdir/.tasks.json |
</phase_requirements>

## Architecture Patterns

### Recommended Project Structure
```
matmaster/tools/builtin/
├── __init__.py          # 导出 BuiltinTool 和所有具体 tool 类
├── base.py              # BuiltinTool ABC
├── bash_tool.py         # BashTool (session-dependent)
├── listdir_tool.py      # ListDirTool (session-dependent)
└── task/
    ├── __init__.py      # 导出 5 个 TaskTool
    ├── _store.py        # TaskStore -- .tasks.json 读写逻辑
    ├── task_create.py   # TaskCreateTool
    ├── task_get.py       # TaskGetTool
    ├── task_list.py      # TaskListTool
    ├── task_update.py    # TaskUpdateTool
    └── task_complete.py  # TaskCompleteTool
```

### Pattern 1: BuiltinTool ABC (模板方法)

**What:** 统一基类，持有 session/workdir 引用，满足 Tool Protocol。子类实现 `_execute()` 模板方法。
**When to use:** 所有 Phase 8/9/11 的 builtin tool。

```python
# matmaster/tools/builtin/base.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


class BuiltinTool(ABC):
    """BuiltinTool base -- satisfies matmaster Tool Protocol.

    Construction injection: session/workdir passed at Exp assemble time.
    Kernel sees only Tool Protocol (name/description/json_schema/execute).

    Subclasses:
    - Define name, description, json_schema as class-level attributes or properties
    - Implement _execute(arguments) -> str
    """

    # Subclass MUST override these (as ClassVar or @property)
    name: ClassVar[str]
    description: ClassVar[str]
    json_schema: ClassVar[dict[str, Any]]

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Path | None = None,
    ) -> None:
        self._session = session
        self._workdir = workdir
        self.logger = logging.getLogger(self.__class__.__name__)

    def execute(self, arguments: dict[str, Any]) -> str:
        """Tool Protocol entry point. Delegates to _execute."""
        try:
            return self._execute(arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str:
        """Subclass implementation. Raise on error, return string on success."""
        ...

    def _require_session(self) -> Any:
        """Guard: raise if session not injected (session-dependent tools)."""
        if self._session is None:
            raise RuntimeError(f"{self.name} requires a session but none was injected")
        return self._session
```

**Key design note:** `name`, `description`, `json_schema` 用 ClassVar 而非 property，因为 Tool Protocol 用 `@runtime_checkable` 检查，ClassVar 属性满足 Protocol 的 property 约束（Python 的 Protocol isinstance 检查不区分 property 和普通属性）。但如果子类需要动态值（未来可能），也可用 @property 覆盖。

### Pattern 2: Session-dependent vs Session-free

**What:** BashTool/ListDirTool 需要 session（远程命令执行），TaskTool 只需要 workdir（本地文件读写）。
**When to use:** 区分两种模式决定构造时的校验策略。

```python
# Session-dependent: BashTool
class BashTool(BuiltinTool):
    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()  # 无 session 则抛 RuntimeError
        # ... 使用 session.exec_bash()

# Session-free: TaskCreateTool
class TaskCreateTool(BuiltinTool):
    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            raise RuntimeError("TaskCreateTool requires workdir")
        store = TaskStore(self._workdir)
        # ... 操作 .tasks.json
```

### Pattern 3: Exp._init_builtin_tools 双源注册

**What:** 改造现有方法，先注册 native BuiltinTool（source="builtin"），再注册 MonitorJobTool 走 EvoToolAdapter（source="builtin_evo"）。

```python
def _init_builtin_tools(
    self, ctx: PlaygroundContext, registry: ToolRegistry
) -> None:
    if ctx.session is None:
        self.logger.warning("No session, skipping builtin tools")
        return

    # 1. Native builtin tools (source="builtin")
    from matmaster.tools.builtin import (
        BashTool, ListDirTool,
        TaskCreateTool, TaskGetTool, TaskListTool,
        TaskUpdateTool, TaskCompleteTool,
    )

    native_tools = [
        BashTool(session=ctx.session, workdir=ctx.workdir),
        ListDirTool(session=ctx.session, workdir=ctx.workdir),
        TaskCreateTool(workdir=ctx.workdir),
        TaskGetTool(workdir=ctx.workdir),
        TaskListTool(workdir=ctx.workdir),
        TaskUpdateTool(workdir=ctx.workdir),
        TaskCompleteTool(workdir=ctx.workdir),
    ]
    for tool in native_tools:
        registry.register(tool, source="builtin")

    # 2. Evo adapter tools (source="builtin_evo") -- transitional
    from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool
    adapted = EvoToolAdapter(MonitorJobTool(), ctx.session)
    registry.register(adapted, source="builtin_evo")

    self.logger.debug(
        "Registered %d native + 1 evo-adapted builtin tools",
        len(native_tools),
    )
```

**注意:** 原有的 evomaster BashTool 和 EditorTool 不再注册。BashTool 由 native 实现替代；EditorTool 延迟到 Phase 9（Read/Write/Edit tools）。

### Anti-Patterns to Avoid
- **在 execute() 中传入 session 参数:** 违反 D-01，Kernel 不应感知 session
- **BuiltinTool 使用 Pydantic frozen model:** BuiltinTool 是有状态的（持有 session 引用），不适合 frozen
- **TaskTool 存储到 cache_area:** CONTEXT.md 明确指定存储到 workdir，因为任务追踪是 workspace 的一部分
- **合并 TaskUpdate 和 TaskComplete:** D-07 明确分离语义

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Bash 命令安全检查 | 自己写正则 | `evomaster.agent.tools.builtin.bash_safety.is_dangerous_bash_command` | 已有完整的危险命令检测，包含 BLOCKED_FIRST_TOKENS 和 DANGEROUS_COMMAND_PATTERNS |
| 代理环境变量清除 | 自己拼字符串 | 复用 `_PROXY_CLEAR_PREFIX` 常量 | 针对 dp.tech 平台特定的代理注入问题 |
| 远程命令执行 | 自己实现 SSH/Docker exec | `session.exec_bash()` | BaseSession 已抽象三种实现（Docker/SSH/Local） |

**Key insight:** BashTool 的核心逻辑不多（约 50 行），但边界条件处理（代理清除、超时、is_input 交互、Windows 兼容）来自 evomaster 的生产经验，应当保留。

## Common Pitfalls

### Pitfall 1: ClassVar 与 Tool Protocol isinstance 检查
**What goes wrong:** 如果用 ClassVar 声明 name/description/json_schema，但 Protocol 用 `@property` 定义了同名属性，isinstance 检查可能不通过。
**Why it happens:** Python 的 `@runtime_checkable` Protocol 在运行时只检查实例是否有对应属性，不检查属性类型。ClassVar 属性在 isinstance 检查时会被 `hasattr(instance, 'name')` 命中，所以实际上可以通过。
**How to avoid:** 用 ClassVar 声明没问题，已验证 MockTool（conftest.py）用 `@property` 和 EvoToolAdapter 都通过了 Protocol 检查。但必须确保子类确实定义了这三个 ClassVar。
**Warning signs:** `isinstance(tool, Tool)` 返回 False。

### Pitfall 2: tasks.json 并发写入
**What goes wrong:** 同一 workspace 下多个 tool call 可能并发读写 .tasks.json 导致数据丢失。
**Why it happens:** AgentKernel 在 ThreadPoolExecutor 中执行 tool.execute()，同步模型但可能有多个 tool_call 并行。
**How to avoid:** TaskStore 使用文件级锁（`fcntl.flock` 或 `threading.Lock`）。实际上 Agent 通常串行调用 tool，但防御性编程更安全。最简方案：TaskStore 内部用 `threading.Lock` 保护 read-modify-write 操作。
**Warning signs:** tasks.json 内容被截断或条目丢失。

### Pitfall 3: evomaster EditorTool 不注册导致 Agent 缺少文件编辑能力
**What goes wrong:** Phase 8 移除了 evomaster BashTool/EditorTool 的注册，但 Phase 9 才交付原生 Read/Write/Edit。中间阶段 Agent 没有文件编辑 tool。
**Why it happens:** 双源注册切换时遗漏了 EditorTool。
**How to avoid:** Phase 8 期间，_init_builtin_tools 应保留 EditorTool 走 EvoToolAdapter（source="builtin_evo"）。仅 BashTool 切换为 native。
**Warning signs:** Agent 尝试调用 editor tool 但 ToolRegistry 中找不到。

### Pitfall 4: workdir 为 None 时 TaskTool 崩溃
**What goes wrong:** 某些测试或特殊场景下 PlaygroundContext.workdir 可能未设置。
**Why it happens:** 测试环境或本地调试可能不完整构造 context。
**How to avoid:** TaskTool._execute 在操作前检查 self._workdir 是否存在，返回友好错误信息而不是 traceback。

### Pitfall 5: BashTool 命令名冲突
**What goes wrong:** evomaster BashTool 的 name 是 `execute_bash`，如果 native BashTool 用不同的 name（如 `bash`），前端或 prompt 中引用旧名称会失败。
**Why it happens:** 命名不统一。
**How to avoid:** native BashTool 保持 `execute_bash` 作为 tool name，与 evomaster 一致。Phase 10 prompt 优化时可以考虑是否改名。

## Code Examples

### BashTool 核心实现参考

```python
# matmaster/tools/builtin/bash_tool.py
from __future__ import annotations

import sys
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool

# 复用 evomaster 的安全检查和代理清除
from evomaster.agent.tools.builtin.bash_safety import is_dangerous_bash_command

_PROXY_CLEAR_PREFIX = (
    'export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= '
    'NO_PROXY= no_proxy= ftp_proxy= FTP_PROXY=; '
    'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY '
    'NO_PROXY no_proxy ftp_proxy FTP_PROXY WGETRC 2>/dev/null; '
)


class BashTool(BuiltinTool):
    name: ClassVar[str] = "execute_bash"
    description: ClassVar[str] = (
        "Execute a bash command in the terminal within a persistent shell session.\n"
        "Commands execute in a persistent session where env vars and working directory persist.\n"
        "For long-running commands, run in background: `cmd > log 2>&1 &`."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "is_input": {
                "type": "string",
                "enum": ["true", "false"],
                "description": "If true, sends input to running process. Default false.",
            },
            "timeout": {
                "type": "number",
                "description": "Hard timeout in seconds. Default uses soft timeout.",
            },
        },
        "required": ["command"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        command = arguments.get("command", "").strip()
        is_input = arguments.get("is_input", "false") == "true"
        timeout_val = arguments.get("timeout", -1)
        timeout = int(timeout_val) if timeout_val > 0 else None

        # Safety check
        is_dangerous, reason = is_dangerous_bash_command(command)
        if is_dangerous:
            return f"Blocked: {reason}"

        # Proxy clear (non-interactive, non-Windows)
        if not is_input and command and sys.platform != "win32":
            command = _PROXY_CLEAR_PREFIX + command

        result = session.exec_bash(
            command=command,
            timeout=timeout,
            is_input=is_input,
        )

        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", -1)
        working_dir = result.get("working_dir", "")

        obs = output
        if working_dir:
            obs += f"\n[Current working directory: {working_dir}]"
        if exit_code != -1:
            obs += f"\n[Command finished with exit code {exit_code}]"

        return obs
```

### ListDirTool 实现参考

```python
# matmaster/tools/builtin/listdir_tool.py
class ListDirTool(BuiltinTool):
    name: ClassVar[str] = "list_dir"
    description: ClassVar[str] = (
        "List the contents of a directory. Returns file names, types, and sizes."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list. Defaults to current working directory.",
            },
        },
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()
        path = arguments.get("path", ".")
        # ls -la 提供详细信息，适合 LLM 理解文件结构
        result = session.exec_bash(command=f'ls -la "{path}"', timeout=10)
        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", -1)
        if exit_code != 0:
            return f"Error listing directory '{path}': {output}"
        return output
```

### TaskStore 和 TaskCreateTool 实现参考

```python
# matmaster/tools/builtin/task/_store.py
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TaskStore:
    """Read/write .tasks.json in workspace directory.

    Thread-safe via internal lock. File format:
    {
      "tasks": {
        "<uuid>": {
          "id": "<uuid>",
          "description": "...",
          "status": "open|in_progress|completed",
          "created_at": "ISO8601",
          "updated_at": "ISO8601"
        }
      }
    }
    """

    _lock = threading.Lock()

    def __init__(self, workdir: Path) -> None:
        self._path = workdir / ".tasks.json"

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"tasks": {}}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def create(self, description: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            task_id = str(uuid.uuid4())[:8]
            now = datetime.now(timezone.utc).isoformat()
            task = {
                "id": task_id,
                "description": description,
                "status": "open",
                "created_at": now,
                "updated_at": now,
            }
            data["tasks"][task_id] = task
            self._write(data)
            return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            return data["tasks"].get(task_id)

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            return list(data["tasks"].values())

    def update(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            task = data["tasks"].get(task_id)
            if task is None:
                return None
            for k, v in fields.items():
                if k in ("description", "status"):
                    task[k] = v
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write(data)
            return task

    def complete(self, task_id: str) -> dict[str, Any] | None:
        return self.update(task_id, status="completed")


# matmaster/tools/builtin/task/task_create.py
class TaskCreateTool(BuiltinTool):
    name: ClassVar[str] = "task_create"
    description: ClassVar[str] = "Create a new task for tracking work progress."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Description of the task to create.",
            },
        },
        "required": ["description"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._workdir is None:
            return "Error: workdir not available for task tracking"
        store = TaskStore(self._workdir)
        task = store.create(arguments["description"])
        return json.dumps(task, ensure_ascii=False)
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (via `uv run pytest`) |
| Config file | `pytest.ini` (root) |
| Quick run command | `uv run pytest tests/matmaster/tools/ -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TOOL-04 | BashTool 执行 shell 命令 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Wave 0 |
| TOOL-04 | BashTool 危险命令拦截 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::test_dangerous_command_blocked -x` | Wave 0 |
| TOOL-07 | ListDirTool 列出目录 | unit | `uv run pytest tests/matmaster/tools/test_listdir_tool.py -x` | Wave 0 |
| TOOL-09 | TaskCreateTool 创建任务 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_create -x` | Wave 0 |
| TOOL-09 | TaskGetTool 查询任务 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_get -x` | Wave 0 |
| TOOL-09 | TaskListTool 列出任务 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_list -x` | Wave 0 |
| TOOL-09 | TaskUpdateTool 更新任务 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_update -x` | Wave 0 |
| TOOL-09 | TaskCompleteTool 完成任务 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_complete -x` | Wave 0 |
| ALL | BuiltinTool 满足 Tool Protocol | unit | `uv run pytest tests/matmaster/tools/test_builtin_base.py -x` | Wave 0 |
| ALL | Exp._init_builtin_tools 双源注册 | unit | `uv run pytest tests/matmaster/core/test_exp.py -x` | 已有文件，需补充测试 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/ -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/tools/test_builtin_base.py` -- BuiltinTool ABC 基类测试（Protocol 满足、_require_session、错误处理）
- [ ] `tests/matmaster/tools/test_bash_tool.py` -- BashTool 单元测试（mock session）
- [ ] `tests/matmaster/tools/test_listdir_tool.py` -- ListDirTool 单元测试（mock session）
- [ ] `tests/matmaster/tools/test_task_tools.py` -- 5 个 TaskTool 单元测试（tmp_path workdir）
- [ ] `tests/matmaster/core/test_exp.py` 需补充 _init_builtin_tools 双源注册的测试用例

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| evomaster BaseTool + EvoToolAdapter | 原生 BuiltinTool ABC | Phase 8 (now) | BashTool/ListDirTool 不再依赖 evomaster |
| 单一 source="builtin" | 双 source: builtin / builtin_evo | Phase 8 (now) | 过渡期可追踪哪些 tool 已迁移 |
| 无 TaskTool | 5 个独立 TaskTool | Phase 8 (now) | Agent 获得工作追踪能力 |

## Open Questions

1. **BashTool tool name 是否保持 `execute_bash`**
   - What we know: evomaster 用 `execute_bash`，前端/prompt 可能硬编码此名称
   - What's unclear: Phase 10 prompt 优化是否会改名
   - Recommendation: Phase 8 保持 `execute_bash`，避免 breaking change

2. **EditorTool 在 Phase 8 是否保留注册**
   - What we know: Phase 8 只替换 BashTool 为 native，EditorTool 延迟到 Phase 9
   - What's unclear: Agent 在 Phase 8-9 间隙是否需要文件编辑能力
   - Recommendation: 保留 EditorTool 走 EvoToolAdapter（source="builtin_evo"），与 MonitorJobTool 同待遇

3. **TaskStore 是否需要跨进程锁**
   - What we know: threading.Lock 只保护同进程并发。生产环境 Worker 单进程执行单 Agent，不存在跨进程并发。
   - Recommendation: threading.Lock 足够，不需要文件锁

## Project Constraints (from CLAUDE.md)

- Python 环境: 始终使用 `uv run` 或 `.venv`
- Import 规范: 全部放文件顶部，按标准库/第三方/本地分组
- 单文件行数: 超过 1000 行需重构
- Worker 模式: 生产环境 run 只在 Worker 上执行

## Sources

### Primary (HIGH confidence)
- `matmaster/tools/tool_registry.py` -- Tool Protocol 定义，ToolRegistry API
- `matmaster/tools/evomaster_tool_adapter.py` -- 构造注入模式参考
- `matmaster/core/exp.py` -- Exp._init_builtin_tools 当前实现
- `matmaster/types/context.py` -- PlaygroundContext 字段定义
- `evomaster/agent/tools/builtin/bash.py` -- BashTool 参考实现
- `evomaster/agent/tools/builtin/bash_safety.py` -- 危险命令检测
- `evomaster/agent/session/base.py` -- BaseSession.exec_bash 接口定义
- `tests/matmaster/tools/` -- 现有测试结构和 MockTool fixture

### Secondary (MEDIUM confidence)
- `.planning/phases/08-builtintool-tools/08-CONTEXT.md` -- 用户锁定决策

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 全部基于项目现有代码和已锁定决策，无外部依赖引入
- Architecture: HIGH - BuiltinTool ABC + 构造注入模式已在 EvoToolAdapter 中验证
- Pitfalls: HIGH - 基于对现有代码的详细分析，特别是 session 依赖和并发模型

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable, project-internal patterns)
