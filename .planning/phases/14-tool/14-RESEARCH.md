# Phase 14: Tool 系统异步化 - Research

**Researched:** 2026-03-27
**Domain:** Python asyncio -- asyncio.to_thread wrapping for sync tool implementations
**Confidence:** HIGH

## Summary

Phase 14 将所有 Tool 的 `execute()` 方法改为 async def，使 ToolRegistry.execute() 可以统一 await 所有 tool。核心策略是在 BuiltinTool ABC 的 `execute()` 模板方法中使用 `asyncio.to_thread(self._execute, arguments)` 包装同步子类实现，14 个具体子类的 `_execute()` 保持 sync def 零改动。LazyMCPTool 和 SkillTool 的 `execute()` 同样改为 async def，内部同步 I/O 用 asyncio.to_thread 包装。

当前代码状态：Phase 12 已将 Tool Protocol 的 execute() 签名改为 async def（tool_registry.py:38），但 BuiltinTool 的实际 execute() 和 _execute() 仍是 sync def（base.py:47/56）。test_builtin_base.py 记录了这个过渡不一致（sync execute 调用 async _execute 返回 coroutine 对象），Phase 14 修复此问题。

实际 BuiltinTool 子类数量为 14 个（非 CONTEXT 中的 12 个），因为 WebSearchTool 和 WebFetchTool 在 CONTEXT 编写后新增。这两个工具同样需要 async 改造，但由于它们是非 session-dependent 的 HTTP 工具，to_thread 包装逻辑相同。

**Primary recommendation:** BuiltinTool.execute() 改为 async def，内部 await asyncio.to_thread(self._execute, arguments)。_execute() 签名回退为 sync def（D-03）。14 个子类零改动。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: BashTool 只做 to_thread 包装 session.exec_bash()，不引入 session-free 模式
- D-02: 在 BuiltinTool.execute() 模板方法中统一用 await asyncio.to_thread(self._execute, arguments) 包装。所有子类 _execute() 保持 sync def 不变
- D-03: BuiltinTool ABC 的 _execute() 签名从 async def 回退为 sync def（Phase 12 改的 async 签名不适用于 to_thread 包装策略）。execute() 改为 async def
- D-04: 异常处理保留在 execute() 模板方法中（try/except 包裹 to_thread 调用），与现有行为一致
- D-05: LazyMCPTool 和 SkillTool 在 Phase 14 一并改为 async def execute()。内部同步调用用 asyncio.to_thread 包装
- D-06: ToolRegistry.execute() 改为 async def，内部 await tool.execute()
- D-07: spawn_fn 保持 sync Callable 类型不变。SpawnTool._execute() 保持 sync def
- D-08: TOOL-05（spawn_fn async callable）实质延后到 Phase 18
- D-09: Kernel 调用 ToolRegistry.execute() 的位置改为 _sync_call_async(registry.execute(...), loop)

### Claude's Discretion
- LazyMCPTool / SkillTool 内部 to_thread 的具体包装方式
- ToolRegistry.execute() 的 normalize_tool_result 调用是否需要适配 async 返回值
- task tools（5 个）的 _execute 签名确认
- 测试迁移的具体范围和 async mock 策略

### Deferred Ideas (OUT OF SCOPE)
- BashTool session-free 模式（asyncio.create_subprocess_exec）
- TOOL-05 spawn_fn async callable -- 延后到 Phase 18
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOOL-01 | 12 个 BuiltinTool 的 execute() 全部改为 async def | D-02/D-03 策略：execute() async + to_thread(_execute)。实际为 14 个子类（含新增 WebSearch/WebFetch） |
| TOOL-02 | BashTool 使用 asyncio.create_subprocess_exec 替代 subprocess.run | D-01 锁定：只用 to_thread 包装 session.exec_bash()。Success Criteria #2 的 session-free 条件不触发 |
| TOOL-03 | 文件操作类 Tool 使用 asyncio.to_thread 包装同步文件 I/O | D-02 统一策略覆盖：execute() 层 to_thread 自动包装所有 _execute 内部 I/O |
| TOOL-04 | session-dependent tool 的 evomaster session 调用使用 asyncio.to_thread 桥接 | D-02 统一策略覆盖：to_thread 在 execute() 层包装整个 _execute，session 调用自然被包含 |
| TOOL-05 | SubAgentTool 的 spawn_fn 改为 async callable | D-08 延后到 Phase 18。Phase 14 只保证 SpawnTool 满足 async Tool Protocol（通过 execute() to_thread 包装自动满足） |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio (stdlib) | Python 3.13 | asyncio.to_thread, event loop | 标准库，项目要求坚持 asyncio |
| pytest-asyncio | >= 0.25.0 | async 测试运行 | 已安装，asyncio_mode=auto 已配置 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | (已有) | ToolResult model | tool 执行结果模型 |

**已安装且已配置:**
- pytest-asyncio: pyproject.toml dev dependency，pytest.ini asyncio_mode=auto
- Python 3.13.2 运行时确认 asyncio.to_thread 可用（Python 3.9+ 特性）

## Architecture Patterns

### 改造后 BuiltinTool 层级结构
```
BuiltinTool (ABC)
├── execute(args) -> async def        # 模板方法：try/except + to_thread
│   └── await asyncio.to_thread(self._execute, arguments)
├── _execute(args) -> sync def        # 子类实现，保持同步
└── _require_session() -> sync def    # 不变

Tool Protocol (runtime_checkable)
├── name: property
├── description: property
├── json_schema: property
└── execute(args) -> async def        # Phase 12 已改为 async
```

### Pattern 1: BuiltinTool.execute() 模板方法改造
**What:** execute() 从 sync def 改为 async def，内部用 asyncio.to_thread 包装 sync _execute()
**When to use:** 所有 14 个 BuiltinTool 子类
**Example:**
```python
# 改造前 (base.py 当前)
def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
    try:
        return self._execute(arguments)
    except Exception as e:
        self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
        return f"Error: {e}"

# 改造后
async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
    try:
        return await asyncio.to_thread(self._execute, arguments)
    except Exception as e:
        self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
        return f"Error: {e}"
```

### Pattern 2: ToolRegistry.execute() async 适配
**What:** ToolRegistry.execute() 改为 async def，await tool.execute()
**When to use:** 统一调度入口
**Example:**
```python
# 改造前
def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
    tool = self._tools.get(name)
    if tool is None:
        ...
    return normalize_tool_result(tool.execute(arguments))

# 改造后
async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
    tool = self._tools.get(name)
    if tool is None:
        ...
    return normalize_tool_result(await tool.execute(arguments))
```

### Pattern 3: LazyMCPTool async 适配
**What:** execute() 改为 async def，内部 connector 和 real_tool 调用用 to_thread 包装
**When to use:** MCP tool 远程调用
**Example:**
```python
# 改造后
async def execute(self, arguments: dict[str, Any]) -> ToolResult:
    if self._real_tool is None:
        self._real_tool = await asyncio.to_thread(
            self._connector.connect_and_get_tool,
            self._server_name, self._remote_tool_name
        )
    args_json = json.dumps(arguments, ensure_ascii=False)
    observation, info = await asyncio.to_thread(
        self._real_tool.execute, self._connector.session, args_json
    )
    # ... result construction same as before
```

### Pattern 4: SkillTool async 适配
**What:** execute() 改为 async def，内部 session.exec_bash 调用通过 to_thread 包装
**When to use:** Skill 操作
**Example:**
```python
# 改造后
async def execute(self, arguments: dict[str, Any]) -> str:
    try:
        skill_name = arguments["skill_name"]
        action = arguments["action"]
        # ... validation
        if action == "get_info":
            return self._get_info(skill)  # 纯内存操作，不需要 to_thread
        elif action == "run_script":
            return await asyncio.to_thread(self._run_script, skill, ...)
        ...
    except Exception as e:
        ...
```

### Pattern 5: Kernel 桥接
**What:** agent.py:247 的 tool dispatch 改为 _sync_call_async 桥接
**When to use:** Phase 17 前过渡期
**Example:**
```python
# 改造前 (agent.py:247)
tool_result = spec.tool_registry.execute(tc.name, tc.arguments)

# 改造后
tool_result = _sync_call_async(
    spec.tool_registry.execute(tc.name, tc.arguments),
    _bridge_loop,
)
```

### Anti-Patterns to Avoid
- **在每个子类 _execute 中单独包装 to_thread:** D-02 明确统一在 execute() 层包装，子类零改动
- **将 _execute 改为 async def:** D-03 明确 _execute 保持 sync def，to_thread 需要同步函数
- **为 WebSearchTool/WebFetchTool 引入 httpx.AsyncClient:** 当前它们使用 sync httpx.Client，to_thread 包装即可。async httpx 可以延后做

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 同步代码异步包装 | 自定义线程池调度 | asyncio.to_thread | stdlib，自动使用默认 executor |
| coroutine 到 sync 桥接 | 手动 loop.run_until_complete | _sync_call_async(coro, loop) | Phase 13 已建立的复用模式 |
| async Protocol 验证 | 手动 inspect 检查 | validate_async_protocol() | Phase 12 已建立的复用 helper |

## Common Pitfalls

### Pitfall 1: asyncio.to_thread 接收 coroutine function 而非 sync function
**What goes wrong:** 如果 _execute 是 async def，asyncio.to_thread 会在线程池中调用它，但不会 await 返回的 coroutine，导致返回 coroutine 对象而非结果
**Why it happens:** to_thread 设计用于包装 sync 函数
**How to avoid:** D-03 明确要求 _execute 保持 sync def。test_builtin_base.py 中使用 async def _execute 的测试需要改回 sync def
**Warning signs:** 测试中 execute() 返回 coroutine 对象而非字符串

### Pitfall 2: normalize_tool_result 接收 awaitable 而非实际值
**What goes wrong:** ToolRegistry.execute() 中 `normalize_tool_result(tool.execute(arguments))` 如果忘记 await，会把 coroutine 传给 normalize_tool_result
**Why it happens:** tool.execute() 变成 async def 后返回 coroutine，必须先 await
**How to avoid:** ToolRegistry.execute() 必须 `result = await tool.execute(arguments)` 然后 `normalize_tool_result(result)`
**Warning signs:** ToolResult.content 包含 `<coroutine object>` 字符串

### Pitfall 3: 测试中直接调用 execute() 忘记 await
**What goes wrong:** 现有测试用 `result = tool.execute(args)` 同步调用，改造后需要 `result = await tool.execute(args)`
**Why it happens:** 现有 20+ 测试文件使用同步调用
**How to avoid:** pytest-asyncio asyncio_mode=auto 已配置，测试方法改为 async def 即可
**Warning signs:** 测试中 result 是 coroutine 对象，assert 失败

### Pitfall 4: LazyMCPConnector 内部已有 asyncio event loop
**What goes wrong:** LazyMCPConnector._ensure_loop() 创建独立 background loop 用于 MCP server 连接。如果 to_thread 中的代码尝试获取当前 event loop，会拿到不同的 loop
**Why it happens:** LazyMCPConnector 有自己的 _loop + _loop_thread
**How to avoid:** to_thread 只包装 connector.connect_and_get_tool() 和 real_tool.execute() 这些纯同步方法调用即可，不需要触碰 connector 的 asyncio loop
**Warning signs:** 嵌套 event loop 错误，或 `RuntimeError: This event loop is already running`

### Pitfall 5: SkillTool._get_info() 中的 on_skill_hit callback 线程安全
**What goes wrong:** 如果 execute() 在 to_thread 中运行整个方法体，on_skill_hit callback 会在 worker thread 中被调用
**Why it happens:** to_thread 在 worker thread 中执行同步函数
**How to avoid:** on_skill_hit callback 目前触发的是 ToolRegistry 注册操作（lazy MCP schema injection），这些操作本身是同步的，在 worker thread 中调用没有线程安全问题。但需要确认 callback 不会触发任何需要 event loop 的操作
**Warning signs:** callback 中的 await 调用失败

### Pitfall 6: WebFetchTool 内部已使用 ThreadPoolExecutor
**What goes wrong:** WebFetchTool._fetch_many() 使用 `ThreadPoolExecutor` 并行获取多个 URL。如果外层 execute() 已经通过 to_thread 在线程池中运行，内层的 ThreadPoolExecutor 会创建线程嵌套
**Why it happens:** 多层线程池嵌套
**How to avoid:** 这实际上是安全的 -- to_thread 使用默认 executor（通常是 ThreadPoolExecutor），内层 WebFetchTool 创建自己的局部 ThreadPoolExecutor。线程嵌套本身没有正确性问题，只是资源效率稍低。Phase 14 不需要优化此处
**Warning signs:** 无，功能正确

## Code Examples

### BuiltinTool.execute() 改造后完整代码
```python
# Source: base.py 改造方案（基于 D-02/D-03/D-04）
import asyncio

class BuiltinTool(ABC):
    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Tool Protocol entry point. Delegates to _execute via to_thread."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Subclass implementation. Sync def. Raise on error."""
        ...
```

### ToolRegistry.execute() 改造后完整代码
```python
# Source: tool_registry.py 改造方案（D-06）
async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
    tool = self._tools.get(name)
    if tool is None:
        available = ", ".join(sorted(self._tools))
        return ToolResult(
            status="error",
            content=f"Error: Tool '{name}' not found. Available: {available}",
        )
    result = await tool.execute(arguments)
    return normalize_tool_result(result)
```

### Kernel 桥接改造
```python
# Source: agent.py:247 改造方案（D-09）
try:
    tool_result = _sync_call_async(
        spec.tool_registry.execute(tc.name, tc.arguments),
        _bridge_loop,
    )
except Exception as e:
    tool_result = ToolResult(
        status="error",
        content=f"Error executing tool '{tc.name}': {type(e).__name__}: {e}",
    )
```

### 测试迁移模式
```python
# 改造前 (sync test)
def test_normal_command_returns_output(self, mock_session):
    tool = BashTool(session=mock_session)
    result = tool.execute({"command": "echo hello"})
    assert "hello world" in result

# 改造后 (async test)
async def test_normal_command_returns_output(self, mock_session):
    tool = BashTool(session=mock_session)
    result = await tool.execute({"command": "echo hello"})
    assert "hello world" in result
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| sync def execute() on all tools | Tool Protocol 声明 async，实际实现仍 sync | Phase 12 | 过渡不一致，Phase 14 修复 |
| 直接调用 tool.execute() | ToolRegistry.execute() 统一调度 | v1.x 架构 | Phase 14 改为 await |

## Open Questions

1. **test_builtin_base.py 中 async _execute 测试**
   - What we know: Phase 12 将 test mock 的 _execute 写成 async def，并有专门测试文档过渡行为
   - What's unclear: D-03 要求 _execute 回退为 sync def，test_builtin_base.py 的 ConcreteBuiltinTool/FailingBuiltinTool 需要同步改回
   - Recommendation: 同时更新测试，删除过渡行为测试 (test_execute_sync_returns_coroutine_not_string)

2. **tests/matmaster/tools/conftest.py MockTool sync execute**
   - What we know: MockTool.execute() 是 sync def，不满足改造后的 async Tool Protocol
   - What's unclear: 是否有测试直接 await MockTool.execute()
   - Recommendation: 将 MockTool.execute() 改为 async def，或者确认所有使用场景通过 ToolRegistry 间接调用

3. **BuiltinTool 子类实际为 14 个（含 WebSearchTool, WebFetchTool）**
   - What we know: CONTEXT 中写的 12 个不含 WebSearchTool 和 WebFetchTool（这两个在 CONTEXT 之后添加）
   - What's unclear: 无，这两个工具的 _execute 已是 sync def，to_thread 包装自动覆盖
   - Recommendation: 计划中列出 14 个子类，确保测试覆盖

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2+ with pytest-asyncio 0.25.0+ |
| Config file | pytest.ini (asyncio_mode=auto) |
| Quick run command | `uv run pytest tests/matmaster/tools/ -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TOOL-01 | 14 个 BuiltinTool execute() 可 await | unit | `uv run pytest tests/matmaster/tools/test_builtin_base.py -x` | Exists (需改造) |
| TOOL-01 | BashTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Exists (需改造) |
| TOOL-01 | ReadTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_read_tool.py -x` | Exists (需改造) |
| TOOL-01 | WriteTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_write_tool.py -x` | Exists (需改造) |
| TOOL-01 | EditTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | Exists (需改造) |
| TOOL-01 | GlobTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_glob_tool.py -x` | Exists (需改造) |
| TOOL-01 | GrepTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_grep_tool.py -x` | Exists (需改造) |
| TOOL-01 | ListDirTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_listdir_tool.py -x` | Exists (需改造) |
| TOOL-01 | SpawnTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_spawn_tool.py -x` | Exists (需改造) |
| TOOL-01 | WebSearchTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_web_search_tool.py -x` | Exists (需改造) |
| TOOL-01 | WebFetchTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_web_fetch_tool.py -x` | Exists (需改造) |
| TOOL-01 | TaskTools (5) execute() async | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py -x` | Exists (需改造) |
| TOOL-02 | BashTool to_thread 包装 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Exists (已覆盖) |
| TOOL-03/04 | session 调用被 to_thread 包装 | unit | (TOOL-01 测试同时覆盖) | Covered |
| TOOL-05 | SpawnTool 满足 async Tool Protocol | unit | `uv run pytest tests/matmaster/tools/test_spawn_tool.py -x` | Exists (需改造) |
| D-05 | LazyMCPTool execute() async | unit | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -x` | Exists (需改造) |
| D-05 | SkillTool execute() async | unit | `uv run pytest tests/test_skill_tool.py -x` | Exists (需改造) |
| D-06 | ToolRegistry.execute() async | unit | `uv run pytest tests/matmaster/tools/test_tool_registry.py -x` | Exists (需改造) |
| D-09 | Kernel 桥接 _sync_call_async | integration | `uv run pytest tests/matmaster/core/test_agent.py -x` | Exists (需改造) |
| Protocol | validate_async_protocol 验证 BuiltinTool | unit | `uv run pytest tests/matmaster/test_validation.py -x` | Exists |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/ tests/matmaster/test_validation.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- None -- all test files exist, need async migration (def -> async def, execute() -> await execute())

## File Impact Inventory

### Source files to modify (16 files)
| File | Change | Scope |
|------|--------|-------|
| `matmaster/tools/builtin/base.py` | execute() async + to_thread, _execute() 签名确认 sync | Core |
| `matmaster/tools/tool_registry.py` | ToolRegistry.execute() async, await tool.execute() | Core |
| `matmaster/tools/lazy_mcp.py` | LazyMCPTool.execute() async + to_thread | Core |
| `matmaster/tools/skill_tool.py` | SkillTool.execute() async + to_thread | Core |
| `matmaster/core/agent.py` | tool dispatch 行改为 _sync_call_async | Bridge |
| (14 builtin tool files) | _execute() 确认 sync def（当前已是 sync，零改动） | Verify only |

### Test files to modify (15+ files)
| File | Change |
|------|--------|
| `tests/matmaster/tools/conftest.py` | MockTool.execute() sync -> async |
| `tests/matmaster/tools/test_builtin_base.py` | ConcreteBuiltinTool/FailingBuiltinTool _execute 改回 sync, 测试改 async |
| `tests/matmaster/tools/test_tool_registry.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_bash_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_read_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_write_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_edit_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_glob_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_grep_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_listdir_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_spawn_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_web_search_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_web_fetch_tool.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_task_tools.py` | test methods async, execute() await |
| `tests/matmaster/tools/test_lazy_mcp.py` | test methods async, execute() await |
| `tests/test_skill_tool.py` | test methods async, execute() await |
| `tests/matmaster/core/test_agent.py` | 如有直接调用 registry.execute() 需适配 |

### Files NOT to modify
| File | Reason |
|------|--------|
| 14 个 builtin tool _execute() 实现 | D-02: 子类 _execute 保持 sync def 零改动 |
| matmaster/core/hooks.py | Phase 15 范围 |
| matmaster/types/runtime.py | 不涉及 |
| matmaster/tools/tool_result.py | normalize_tool_result 接收的是值不是 coroutine |

## Sources

### Primary (HIGH confidence)
- 项目源码直接审查：base.py, tool_registry.py, lazy_mcp.py, skill_tool.py, agent.py, 14 个 builtin tool 实现文件
- Python 3.13 stdlib asyncio.to_thread -- 运行时验证可用
- pytest.ini -- asyncio_mode=auto 已配置
- 14-CONTEXT.md -- 锁定决策 D-01 至 D-09

### Secondary (MEDIUM confidence)
- test_builtin_base.py -- 过渡行为文档，确认 Phase 12 状态
- tests/conftest.py -- MockAsyncTool 模式（Phase 12 建立）

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - asyncio stdlib + pytest-asyncio 均已安装验证
- Architecture: HIGH - 基于直接代码审查和锁定决策，改造路径明确
- Pitfalls: HIGH - 基于代码审查识别的具体风险点，均有明确解决方案

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (stable domain, locked decisions)
