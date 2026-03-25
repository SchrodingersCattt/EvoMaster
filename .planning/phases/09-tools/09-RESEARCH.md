# Phase 9: 文件操作 Tools - Research

**Researched:** 2026-03-25
**Domain:** BuiltinTool file operations (Read/Write/Edit/Glob/Grep) + Read-Before-Modify protocol + EditorTool removal + ExpConfig explicit enumeration
**Confidence:** HIGH

## Summary

Phase 9 交付 5 个文件操作 BuiltinTool（ReadTool, WriteTool, EditTool, GlobTool, GrepTool），外加一个 ReadTracker 共享状态对象实现 Read-Before-Modify 协议，并完成 EditorTool 移除和 ExpConfig.tools.builtin 从 wildcard 切换到显式列举。

所有代码基础设施已在 Phase 8 建立并经 101 个测试验证通过。BuiltinTool ABC、Tool Protocol、ToolRegistry、session 接口（read_file/write_file/exec_bash/is_file/is_directory/path_exists）均已就绪。evomaster EditorTool 的 `_view`、`_create`、`_str_replace` 逻辑提供了完整的迁移参考源。核心工作是将 evomaster 的单体 EditorTool 拆分为 5 个独立工具，增加 ReadTracker 协议层，并清理 Exp 注册路径。

**Primary recommendation:** 按 ReadTracker -> ReadTool -> WriteTool -> EditTool -> GlobTool -> GrepTool -> Exp 改造 -> ExpConfig 切换 的依赖顺序实现。ReadTracker 是 Read/Write/Edit 的前置依赖，必须最先交付。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Edit tool 仅保留 str_replace 一个能力，对齐 Claude Code 设计。Write tool 覆盖全文覆写场景，str_replace 覆盖精确编辑场景。不保留 insert（行号漂移风险）和 undo_edit（BuiltinTool 构造注入模型不保证 _file_history 跨 assemble 存活）。
- **D-02:** 采用共享 ReadTracker 注入方案。Exp.assemble() 创建单一 ReadTracker 实例（内部维护 `_read_files: set[str]`），通过构造注入传给 Read/Write/Edit 三个 tool。ReadTool 执行时向 tracker 注册文件路径，WriteTool/EditTool 执行前检查 tracker。
- **D-03:** 违反协议时 WriteTool/EditTool 的 _execute 返回 `"Error: file '{path}' must be read before modify"` 字符串，符合 base.py 现有错误返回约定。
- **D-04:** ReadTracker 生命周期跟随 Exp run（assemble 时创建，cleanup 时销毁），per-run 状态不跨 run 保留。
- **D-05:** Glob/Grep 通过 session.exec_bash() 包装 find/grep 命令实现，复用已验证的远程执行路径，与 ListDirTool/BashTool 模式一致。远程环境（Bohrium 节点）文件系统只能通过 exec_bash 触达。
- **D-06:** 搜索范围强制限制在 workdir 内。所有搜索路径强制拼接 workdir 前缀，防止 path traversal。与 BashTool（无限制）不同，文件操作 tool 有明确边界。
- **D-07:** 输出通过 `| head -N` 截断防止 token 爆炸。GlobTool 封装 find，GrepTool 封装 grep -rn。
- **D-08:** Phase 9 内原子化完成 EditorTool 移除。新 native tools 交付后，同步从 _init_builtin_tools() 移除 EditorTool 的 EvoToolAdapter 注册路径。MonitorJobTool 保留 evo adapter 路径不变。
- **D-09:** ExpConfig.tools.builtin 从 wildcard `"*"` 切换到显式列举（列出所有 native tool 名称）。在 Phase 9 内完成，不留 Phase 10。

### Claude's Discretion
- GlobTool/GrepTool 的具体 find/grep 命令参数设计（maxdepth、include 模式等）
- ReadTracker 的具体实现（独立类 vs 简单 set wrapper）
- Read/Write/Edit 各 tool 的 json_schema 参数细节
- 输出截断的具体行数阈值（head -N 的 N 值）
- _init_builtin_tools 拆分后 MonitorJobTool 的注册代码组织方式

### Deferred Ideas (OUT OF SCOPE)
- MonitorJobTool 原生化 -- 当前保留 evo adapter 路径，评估是否需要迁移
- Read tool 支持 PDF/图片等非文本文件 -- 当前场景非核心
- Edit tool 的 replace_all 模式 -- 当前只支持唯一匹配替换，批量替换后续评估
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOOL-01 | Agent 可以通过 Read tool 读取远程文件内容（支持行范围指定） | ReadTool 通过 session.read_file() 读取，view_range 参数支持行范围切片，迁移自 EditorTool._view() |
| TOOL-02 | Agent 可以通过 Write tool 创建或覆盖远程文件 | WriteTool 通过 session.write_file() 写入，迁移自 EditorTool._create()，需 ReadTracker 检查 |
| TOOL-03 | Agent 可以通过 Edit tool 对远程文件进行精确字符串替换 | EditTool 通过 session.read_file() + session.write_file() 实现 str_replace，迁移自 EditorTool._str_replace()，需 ReadTracker 检查 |
| TOOL-05 | Agent 可以通过 Glob tool 按模式搜索远程文件路径 | GlobTool 通过 session.exec_bash() 封装 find 命令，workdir 限制，head 截断 |
| TOOL-06 | Agent 可以通过 Grep tool 按正则搜索远程文件内容 | GrepTool 通过 session.exec_bash() 封装 grep -rn 命令，workdir 限制，head 截断 |
| TOOL-08 | Write/Edit tool 执行前强制要求先 Read 目标文件（Read-Before-Modify 协议） | ReadTracker 共享实例，构造注入 Read/Write/Edit，Read 注册路径，Write/Edit 检查路径 |
</phase_requirements>

## Standard Stack

### Core

Phase 9 不引入新外部依赖。全部工具使用项目现有基础设施。

| Component | Location | Purpose | Why Standard |
|-----------|----------|---------|--------------|
| BuiltinTool ABC | `matmaster/tools/builtin/base.py` | 所有 tool 基类 | Phase 8 已建立，101 tests 验证 |
| Tool Protocol | `matmaster/tools/tool_registry.py` | 统一接口 | name/description/json_schema/execute |
| BaseSession | `evomaster/agent/session/base.py` | 远程操作接口 | read_file/write_file/exec_bash/is_file/is_directory/path_exists |
| ToolRegistry | `matmaster/tools/tool_registry.py` | 工具注册 | register(tool, source) + get_tool_definitions() |

### Supporting

| Component | Location | Purpose | When to Use |
|-----------|----------|---------|-------------|
| maybe_truncate | `evomaster/agent/tools/builtin/editor.py` | 输出截断 | ReadTool 文件内容过长时中间截断 |
| EditorTool | `evomaster/agent/tools/builtin/editor.py` | 迁移参考 | _view/_create/_str_replace 逻辑提取 |
| EvoToolAdapter | `matmaster/tools/evomaster_tool_adapter.py` | MonitorJobTool 适配 | 仅 MonitorJobTool 继续使用 |

### No New Dependencies

Phase 9 不需要 `pip install` 任何新包。全部实现基于 Python 标准库 + 项目已有代码。

## Architecture Patterns

### Recommended File Structure

```
matmaster/tools/builtin/
├── __init__.py          # 导出新增 5 tool + ReadTracker
├── base.py              # BuiltinTool ABC (不修改)
├── bash_tool.py         # BashTool (不修改)
├── listdir_tool.py      # ListDirTool (不修改)
├── read_tracker.py      # NEW: ReadTracker 共享状态
├── read_tool.py         # NEW: ReadTool
├── write_tool.py        # NEW: WriteTool
├── edit_tool.py         # NEW: EditTool
├── glob_tool.py         # NEW: GlobTool
├── grep_tool.py         # NEW: GrepTool
└── task/                # Task 工具套件 (不修改)
```

### Pattern 1: ReadTracker 共享状态注入

**What:** 轻量独立类，维护 `_read_files: set[str]`，通过构造函数注入 Read/Write/Edit 三个 tool。不修改 BuiltinTool 基类签名。

**When to use:** 任何需要跨 tool 共享状态的场景。

**Implementation approach:**

```python
# matmaster/tools/builtin/read_tracker.py

class ReadTracker:
    """Track which files have been read in the current run.

    Shared instance injected into Read/Write/Edit tools at Exp assemble time.
    Lifecycle: created at assemble, cleared at cleanup.
    """

    def __init__(self) -> None:
        self._read_files: set[str] = set()

    def mark_read(self, path: str) -> None:
        """Record that a file has been read."""
        self._read_files.add(path)

    def has_been_read(self, path: str) -> bool:
        """Check if a file has been read."""
        return path in self._read_files

    def clear(self) -> None:
        """Reset tracked state (called at cleanup)."""
        self._read_files.clear()
```

**Key design point:** ReadTracker 是独立类，不是 BuiltinTool 基类的一部分。Read/Write/Edit tool 在 `__init__` 中额外接受 `tracker: ReadTracker | None = None` 参数（keyword-only），不影响 GlobTool/GrepTool 等无需 tracker 的 tool。

### Pattern 2: 文件操作 Tool 构造签名

**What:** Read/Write/Edit 三个 tool 在 BuiltinTool 的 session/workdir 基础上额外接受 tracker 参数。

**Implementation approach:**

```python
class ReadTool(BuiltinTool):
    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Path | None = None,
        tracker: ReadTracker | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._tracker = tracker
```

**Key point:** tracker 默认 None，保持与 BuiltinTool 构造的一致性（可无依赖实例化用于测试）。

### Pattern 3: workdir 路径安全（Glob/Grep）

**What:** 所有搜索路径强制限制在 workdir 内，防止 path traversal。

**Implementation approach:**

```python
def _resolve_safe_path(self, user_path: str) -> str:
    """Resolve user path within workdir, preventing traversal."""
    workdir = str(self._workdir) if self._workdir else "/workspace"
    if not user_path or user_path == ".":
        return workdir
    # 如果是绝对路径，检查是否在 workdir 下
    if user_path.startswith("/"):
        # posixpath.normpath 解析 ..
        import posixpath
        normalized = posixpath.normpath(user_path)
        if not normalized.startswith(workdir):
            return workdir  # 拒绝越界，回退到 workdir
        return normalized
    # 相对路径：拼接到 workdir
    import posixpath
    joined = posixpath.join(workdir, user_path)
    normalized = posixpath.normpath(joined)
    if not normalized.startswith(workdir):
        return workdir
    return normalized
```

### Pattern 4: exec_bash 输出提取

**What:** session.exec_bash() 返回 dict，不同 session 实现的 key 不一致（output vs stdout）。

**Established convention** (from BashTool line 80):
```python
output = result.get("output", "") or result.get("stdout", "")
```

所有新 tool 使用此双 key 提取模式。

### Anti-Patterns to Avoid

- **修改 BuiltinTool 基类签名:** ReadTracker 只注入需要它的 tool（Read/Write/Edit），不改 base.py。GlobTool/GrepTool/BashTool/ListDirTool/TaskTools 保持现有构造签名不变。
- **在 tool 内部创建 ReadTracker:** ReadTracker 由 Exp.build_runtime 创建并注入，tool 不自建实例。这保证同一 run 内 Read/Write/Edit 共享同一个 tracker。
- **EditorTool 的 _file_history 迁移:** D-01 明确不保留 undo_edit，不需要 _file_history。WriteTool 是全文覆写，无需保留旧内容。
- **Glob/Grep 使用 session.read_file 逐文件扫描:** 远程环境只能通过 exec_bash 触达文件系统。用 find/grep 命令是唯一可行路径。
- **路径参数允许绝对路径越界 workdir:** Glob/Grep 必须有路径安全检查，ReadTool/WriteTool/EditTool 不限制（与 EditorTool 行为一致，它们操作绝对路径）。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 输出截断 | 自写截断逻辑 | `maybe_truncate()` from `evomaster/agent/tools/builtin/editor.py` | 已处理中间截断、notice 拼接 |
| 远程文件读取 | 自写 SFTP/SCP 逻辑 | `session.read_file(path)` | BaseSession 已封装 download+decode |
| 远程文件写入 | 自写文件上传逻辑 | `session.write_file(path, content)` | BaseSession 已封装 encode+upload |
| 远程路径检查 | 自写 stat 命令 | `session.is_file(path)` / `session.path_exists(path)` | BaseSession 已封装 test -f/-e 命令 |
| 远程命令执行 | 自写 SSH 命令 | `session.exec_bash(command)` | BaseSession 已封装所有传输层 |
| 字符串唯一匹配替换 | 自写文本处理 | 迁移 EditorTool._str_replace 核心逻辑 | 已处理 re.escape、多匹配检测、行号计算、strip fallback |

## Common Pitfalls

### Pitfall 1: ReadTracker 路径标准化

**What goes wrong:** ReadTool 注册路径 `/workspace/foo.py`，但 WriteTool 检查路径 `./foo.py` 或 `foo.py`，导致协议误判。
**Why it happens:** 用户（LLM）传入的路径格式不一致。
**How to avoid:** ReadTool.mark_read 和 WriteTool/EditTool.has_been_read 都使用标准化后的路径。最简方案：使用 `posixpath.normpath()` 标准化。如果有 workdir，将相对路径解析为绝对路径后再比较。
**Warning signs:** 测试中 Read 了文件但 Write 仍报 "must be read before modify"。

### Pitfall 2: exec_bash 输出 key 不一致

**What goes wrong:** 使用 `result["stdout"]` 直接取值，某些 session 实现返回 `output` key。
**Why it happens:** BaseSession 定义返回 dict 含 stdout，但 SSH/Docker 实现的 key 不完全一致。
**How to avoid:** 统一使用 `result.get("output", "") or result.get("stdout", "")` 双 key 模式。BashTool 已确立此约定。
**Warning signs:** Glob/Grep 在特定 session 类型下返回空结果。

### Pitfall 3: Grep 正则特殊字符

**What goes wrong:** 用户搜索 `foo.bar` 时 `.` 被解释为正则通配符。
**Why it happens:** grep 默认使用 BRE/ERE 正则。
**How to avoid:** GrepTool 使用 `grep -rn` 配合用户提供的 pattern（不做额外 escape）。在 tool description 中明确告知 LLM pattern 是正则表达式。如果需要字面量搜索，LLM 自行 escape。
**Warning signs:** 搜索结果包含非预期匹配。

### Pitfall 4: EditorTool 移除后 tool name 冲突

**What goes wrong:** EditorTool 注册名为 `str_replace_editor`，新 tool 使用不同名称（如 `read_file`、`write_file`、`edit_file`）。如果移除不彻底，ToolRegistry 中可能残留旧 tool。
**Why it happens:** _init_builtin_tools 改造不完整。
**How to avoid:** 明确移除 EditorTool import 和 EvoToolAdapter 包装行。MonitorJobTool 单独保留。测试验证 registry 中不含 `str_replace_editor`。
**Warning signs:** registry.get_tool_definitions() 输出中出现 `str_replace_editor`。

### Pitfall 5: ReadTracker 清理时机

**What goes wrong:** run() 结束后 ReadTracker 状态未清理，下一次 run 时上次 Read 的文件被错误认为 "已读"。
**Why it happens:** ReadTracker.clear() 未注册到 _cleanup_callbacks。
**How to avoid:** D-04 明确 per-run 生命周期。build_runtime 中创建 ReadTracker 后，将 tracker.clear 注册到 self._register_cleanup()。
**Warning signs:** 连续两次 run 时第二次 Write 不需要先 Read。

### Pitfall 6: GlobTool find 命令兼容性

**What goes wrong:** 使用 GNU find 特有语法（如 `-regex`），在某些精简容器中不可用。
**Why it happens:** 远程 Bohrium 节点可能是最小化 Linux 镜像。
**How to avoid:** 使用 POSIX 兼容的 find 参数：`-name`（glob 模式）、`-maxdepth`、`-type f`。不使用 `-regex`、`-iregex` 等 GNU 扩展。
**Warning signs:** find 命令返回 "unknown predicate" 错误。

## Code Examples

### ReadTool 核心实现参考

迁移自 EditorTool._view() + `_format_output()`：

```python
# Source: evomaster/agent/tools/builtin/editor.py lines 234-306, 460-470

class ReadTool(BuiltinTool):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read the contents of a file. Use line_range to read specific lines."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "line_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional [start, end] line range (1-indexed). Omit to read entire file.",
            },
        },
        "required": ["file_path"],
    }

    def __init__(self, *, session=None, workdir=None, tracker=None):
        super().__init__(session=session, workdir=workdir)
        self._tracker = tracker

    def _execute(self, arguments):
        session = self._require_session()
        file_path = arguments["file_path"]
        line_range = arguments.get("line_range")

        # 验证路径存在且是文件
        if not session.is_file(file_path):
            return f"Error: {file_path} is not a file or does not exist."

        content = session.read_file(file_path)

        # 注册到 tracker
        if self._tracker is not None:
            self._tracker.mark_read(self._normalize_path(file_path))

        # 处理行范围
        if line_range:
            # ... 行范围切片逻辑（迁移自 EditorTool._view）
            pass

        return self._format_with_line_numbers(content, file_path)
```

### WriteTool Read-Before-Modify 检查

```python
# Source: CONTEXT.md D-02, D-03

class WriteTool(BuiltinTool):
    name: ClassVar[str] = "write_file"

    def _execute(self, arguments):
        session = self._require_session()
        file_path = arguments["file_path"]
        content = arguments["content"]

        # Read-Before-Modify 协议检查
        normalized = self._normalize_path(file_path)
        if self._tracker is not None:
            # 新文件（不存在）不需要先 Read
            if session.path_exists(file_path) and not self._tracker.has_been_read(normalized):
                return f"Error: file '{file_path}' must be read before modify"

        session.write_file(file_path, content)
        return f"File written successfully to: {file_path}"
```

### EditTool str_replace 核心逻辑

迁移自 EditorTool._str_replace()，去掉 undo_edit 相关代码：

```python
# Source: evomaster/agent/tools/builtin/editor.py lines 327-398

class EditTool(BuiltinTool):
    name: ClassVar[str] = "edit_file"

    def _execute(self, arguments):
        session = self._require_session()
        file_path = arguments["file_path"]
        old_str = arguments["old_str"]
        new_str = arguments["new_str"]

        # Read-Before-Modify 检查
        normalized = self._normalize_path(file_path)
        if self._tracker is not None and not self._tracker.has_been_read(normalized):
            return f"Error: file '{file_path}' must be read before modify"

        content = session.read_file(file_path)

        # 唯一匹配检查 + 替换（迁移自 EditorTool._str_replace）
        import re
        pattern = re.escape(old_str)
        matches = list(re.finditer(pattern, content))
        # ... 0 match / multi match / strip retry 逻辑
```

### GlobTool find 命令封装

```python
# Source: ListDirTool pattern (exec_bash wrapper)

class GlobTool(BuiltinTool):
    name: ClassVar[str] = "glob"

    def _execute(self, arguments):
        session = self._require_session()
        pattern = arguments["pattern"]
        path = arguments.get("path", ".")

        safe_path = self._resolve_safe_path(path)
        # POSIX-compatible find with name glob
        cmd = f'find "{safe_path}" -type f -name "{pattern}" 2>/dev/null | head -200'
        result = session.exec_bash(command=cmd, timeout=30)
        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", -1)

        if not output.strip():
            return f"No files matching pattern '{pattern}' found in {safe_path}"
        return output
```

### GrepTool grep 命令封装

```python
class GrepTool(BuiltinTool):
    name: ClassVar[str] = "grep"

    def _execute(self, arguments):
        session = self._require_session()
        pattern = arguments["pattern"]
        path = arguments.get("path", ".")

        safe_path = self._resolve_safe_path(path)
        include = arguments.get("include", "")  # e.g., "*.py"

        include_flag = f'--include="{include}"' if include else ""
        cmd = f'grep -rn {include_flag} "{pattern}" "{safe_path}" 2>/dev/null | head -200'
        result = session.exec_bash(command=cmd, timeout=30)
        output = result.get("output", "") or result.get("stdout", "")

        if not output.strip():
            return f"No matches for pattern '{pattern}' in {safe_path}"
        return output
```

### Exp._init_builtin_tools 改造后

```python
# Source: matmaster/core/exp.py lines 232-282 (改造目标)

def _init_builtin_tools(self, ctx, registry):
    if ctx.session is None:
        self.logger.warning("No session, skipping builtin tools")
        return

    from matmaster.tools.builtin import (
        BashTool, ListDirTool,
        TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool, TaskCompleteTool,
        ReadTool, WriteTool, EditTool, GlobTool, GrepTool, ReadTracker,
    )

    # 创建共享 ReadTracker
    tracker = ReadTracker()
    self._register_cleanup(tracker.clear)

    native_tools = [
        BashTool(session=ctx.session, workdir=ctx.workdir),
        ListDirTool(session=ctx.session, workdir=ctx.workdir),
        ReadTool(session=ctx.session, workdir=ctx.workdir, tracker=tracker),
        WriteTool(session=ctx.session, workdir=ctx.workdir, tracker=tracker),
        EditTool(session=ctx.session, workdir=ctx.workdir, tracker=tracker),
        GlobTool(session=ctx.session, workdir=ctx.workdir),
        GrepTool(session=ctx.session, workdir=ctx.workdir),
        TaskCreateTool(workdir=ctx.workdir),
        TaskGetTool(workdir=ctx.workdir),
        TaskListTool(workdir=ctx.workdir),
        TaskUpdateTool(workdir=ctx.workdir),
        TaskCompleteTool(workdir=ctx.workdir),
    ]
    for tool in native_tools:
        registry.register(tool, source="builtin")

    # MonitorJobTool: 保留 evo adapter 路径
    from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool
    adapted = EvoToolAdapter(MonitorJobTool(), ctx.session)
    registry.register(adapted, source="builtin_evo")
```

### ExpConfig.tools.builtin 显式列举

```toml
# matmaster/exps/direct.toml 改造后

[tools]
builtin = [
    "execute_bash",
    "list_dir",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "task_complete",
]
```

同时需要改造 `build_runtime` 中的条件判断：

```python
# 旧: if "*" in builtin_cfg and ctx.session is not None:
# 新: if builtin_cfg and ctx.session is not None:
#     或者保持 wildcard 兼容: if ("*" in builtin_cfg or builtin_cfg) and ctx.session is not None:
```

**注意:** 当前 `build_runtime` 仅检查 `"*" in builtin_cfg`，切换到显式列举后此条件为 False，_init_builtin_tools 不会被调用。必须同步更新此条件判断。

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| EditorTool 单体（5 个 command） | 5 个独立 BuiltinTool | Phase 9 | LLM 调用更清晰，每个 tool schema 更简单 |
| EvoToolAdapter 包装 EditorTool | native BuiltinTool 直接满足 Tool Protocol | Phase 9 | 减少一层间接调用，session 构造注入 |
| `tools.builtin = ["*"]` wildcard | 显式列举所有 tool 名称 | Phase 9 | 可配置启用/禁用特定 tool |
| 无写前读检查 | Read-Before-Modify 协议 | Phase 9 | 防止 LLM 盲写覆盖文件 |

## Open Questions

1. **ReadTool 路径标准化策略**
   - What we know: ReadTracker 需要路径标准化才能正确匹配。远程环境路径都是 POSIX 风格。
   - What's unclear: 是否需要处理符号链接解析（realpath）。EditorTool 使用 `_normalize_path` 但主要处理 Windows 兼容性。
   - Recommendation: 使用 `posixpath.normpath()` 即可。远程环境是 Linux，不需要 Windows 路径处理。符号链接解析需要额外的 exec_bash 调用，成本高收益低，暂不处理。

2. **WriteTool 新文件创建是否需要 Read 检查**
   - What we know: D-02 说 "WriteTool 执行前检查 tracker"，但新文件（不存在的文件）显然不需要先 Read。
   - What's unclear: CONTEXT.md 未明确 "新文件豁免" 的细节。
   - Recommendation: 使用 `session.path_exists(file_path)` 判断。如果文件不存在，跳过 ReadTracker 检查（创建新文件不需要先 Read）。如果文件已存在，强制要求先 Read。这与 Read-Before-Modify 的语义一致：Modify 意味着改已有文件。

3. **build_runtime 中 builtin_cfg 条件判断的更新方式**
   - What we know: 当前代码 `if "*" in builtin_cfg`，切换到显式列举后此条件不再成立。
   - What's unclear: 是否应该保留 wildcard 兼容（both `"*"` and explicit list work），还是完全移除 wildcard 支持。
   - Recommendation: 改为 `if builtin_cfg and ctx.session is not None`（非空列表即触发）。保留 wildcard 作为 "all" 的简写也可以，但 D-09 明确要显式列举，建议同时去掉 wildcard 支持以保持简洁。

4. **输出截断的具体阈值**
   - What we know: EditorTool 使用 `MAX_OUTPUT_SIZE = 16000` 字符（maybe_truncate），head 用于行数截断。
   - What's unclear: head -N 的 N 值对不同场景的合理值。
   - Recommendation: GlobTool/GrepTool 使用 `head -200`（200 行），ReadTool 使用 `maybe_truncate(content, max_size=16000)` 与 EditorTool 保持一致。Claude's discretion 允许调整。

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (via uv run) |
| Config file | `pytest.ini` |
| Quick run command | `uv run pytest tests/matmaster/tools/ -x --tb=short -q` |
| Full suite command | `uv run pytest tests/ -x --tb=short` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TOOL-01 | ReadTool 读取远程文件 + 行范围 | unit | `uv run pytest tests/matmaster/tools/test_read_tool.py -x` | Wave 0 |
| TOOL-02 | WriteTool 创建/覆写远程文件 | unit | `uv run pytest tests/matmaster/tools/test_write_tool.py -x` | Wave 0 |
| TOOL-03 | EditTool str_replace 精确替换 | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | Wave 0 |
| TOOL-05 | GlobTool find 搜索文件路径 | unit | `uv run pytest tests/matmaster/tools/test_glob_tool.py -x` | Wave 0 |
| TOOL-06 | GrepTool grep 搜索文件内容 | unit | `uv run pytest tests/matmaster/tools/test_grep_tool.py -x` | Wave 0 |
| TOOL-08 | Read-Before-Modify 协议 | unit | `uv run pytest tests/matmaster/tools/test_read_tracker.py -x` | Wave 0 |
| INT-01 | Exp._init_builtin_tools 注册全部 native tools | integration | `uv run pytest tests/matmaster/core/test_exp_builtin_registration.py -x` | Wave 0 |
| INT-02 | EditorTool 已移除 + ExpConfig 显式列举 | integration | `uv run pytest tests/matmaster/core/test_exp_builtin_registration.py -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/matmaster/tools/ -x --tb=short -q`
- **Per wave merge:** `uv run pytest tests/ -x --tb=short`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/matmaster/tools/test_read_tool.py` -- covers TOOL-01 (ReadTool basic/line_range/format/protocol)
- [ ] `tests/matmaster/tools/test_write_tool.py` -- covers TOOL-02 (WriteTool create/overwrite/read-before-modify)
- [ ] `tests/matmaster/tools/test_edit_tool.py` -- covers TOOL-03 (EditTool str_replace/unique_match/read-before-modify)
- [ ] `tests/matmaster/tools/test_glob_tool.py` -- covers TOOL-05 (GlobTool find/workdir_safety/truncation)
- [ ] `tests/matmaster/tools/test_grep_tool.py` -- covers TOOL-06 (GrepTool grep/workdir_safety/truncation)
- [ ] `tests/matmaster/tools/test_read_tracker.py` -- covers TOOL-08 (ReadTracker mark/check/clear/normalization)
- [ ] `tests/matmaster/core/test_exp_builtin_registration.py` -- covers INT-01/INT-02 (Exp integration)

## Sources

### Primary (HIGH confidence)

- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC 接口定义，Phase 8 交付
- `matmaster/tools/builtin/bash_tool.py` -- exec_bash 用法参考，双 key 输出提取模式
- `matmaster/tools/builtin/listdir_tool.py` -- exec_bash 包装 find 命令的模式
- `matmaster/tools/tool_registry.py` -- Tool Protocol 定义，ToolRegistry API
- `matmaster/core/exp.py` -- _init_builtin_tools() 当前实现，改造目标
- `matmaster/config/exp.py` -- ExpToolsConfig.builtin 字段定义
- `evomaster/agent/tools/builtin/editor.py` -- EditorTool 完整实现（迁移参考源）
- `evomaster/agent/session/base.py` -- BaseSession 接口（read_file/write_file/exec_bash/is_file 等）
- `.planning/phases/09-tools/09-CONTEXT.md` -- Phase 9 所有锁定决策
- `.planning/phases/08-builtintool-tools/08-CONTEXT.md` -- Phase 8 决策（BuiltinTool 设计、注册切换策略）

### Secondary (MEDIUM confidence)

- `matmaster/exps/direct.toml` -- 当前 ExpConfig 配置（需改造）
- `tests/matmaster/tools/test_bash_tool.py` -- 测试模式参考（mock_session fixture 结构）
- `tests/matmaster/tools/test_listdir_tool.py` -- 测试模式参考

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 全部使用项目已有基础设施，无新依赖
- Architecture: HIGH -- BuiltinTool ABC 模式已在 Phase 8 确立并验证，5 个新 tool 遵循相同模式
- Pitfalls: HIGH -- 路径标准化、exec_bash key 不一致等问题已在现有代码中观察到解决方案

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable -- 项目内部架构，不受外部更新影响)
