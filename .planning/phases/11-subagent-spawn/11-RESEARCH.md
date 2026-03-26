# Phase 11: SubAgent Spawn 机制 - Research

**Researched:** 2026-03-25
**Domain:** SubAgent spawn mechanism -- tool-based child agent execution within parent agent loop
**Confidence:** HIGH

## Summary

Phase 11 在已完成的 BuiltinTool 基础设施（Phase 8）、文件操作 Tool（Phase 9）和 Tool Description/System Prompt（Phase 10）之上，实现 SubAgent spawn 机制。核心是新增 SubAgentTool（继承 BuiltinTool ABC），通过 spawn_fn 闭包注入实现与 Exp 层的解耦。子 agent 复用 AgentKernel 执行循环，通过独立 ExpConfig（TOML 定义）获得独立的 tool 集和 system prompt，同时共享父 agent 的 PlaygroundContext（workspace/session）和 MessageBus。

关键技术点：spawn_fn 闭包在 Exp.build_runtime() 中创建，捕获 ctx/bus 等运行时上下文，SubAgentTool 通过闭包间接调用 Exp 层创建子 agent。递归保护采用双层机制：Schema 层（子 exp TOML 不包含 sub_agent）+ 运行时层（spawn_fn=None 守卫）。stop_event 通过共享 threading.Event 实现级联取消。子 agent 事件通过带前缀 source（如 MatMaster:explore）emit 到父 MessageBus，需扩展 normalize_event_source 和 _normalize_public_source 两处来兼容前缀格式。

**Primary recommendation:** 按三层顺序实现：(1) SubAgentTool 类 + 子 exp TOML 定义, (2) Exp 层 spawn_fn 闭包创建 + 工具注册, (3) source 前缀事件路由兼容改造。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 动态 exp 选择方案。SubAgentTool schema 为 `execute({"exp_name": "explore", "task": "..."})` 。LLM 通过 exp_name 参数指定子 agent 类型（如 explore/research），task 参数传递任务描述。不引入额外 context 参数，父 agent 将上下文写入 task 文本。后续对齐 Claude Code 的多种内置 subagent 类型（Agent tool with subagent_type）。
- **D-02:** 共享父 MessageBus + source 前缀区分来源。子 agent 的 EventEmitterHook 构造时传入带前缀的 source（如 `MatMaster:explore`），直接 emit 到父 MessageBus。`normalize_event_source` 扩展规则以保留 `MatMaster:*` 前缀格式。`chat_history.py` 中 source 判断需同步更新以兼容前缀格式。前端可通过 source 前缀区分父/子 agent 事件渲染。
- **D-03:** 独立 TOML 文件。每个子 exp 类型一个 TOML（如 `matmaster/exps/explore.toml`、`matmaster/exps/research.toml`），独立定义 tools.builtin 列表和 developer_instructions（PRMT-03）。通过现有 `load_exp_config(name)` 加载，零改动加载链路。子 exp 的 system prompt 针对子任务场景设计，写入 TOML 的 developer_instructions 字段，沿用 ContextBuilder 的 identity section。
- **D-04:** 双层保护机制。Schema 层：子 exp TOML 的 `tools.builtin` 列表不包含 `sub_agent`，LLM 完全不可见 SubAgentTool。运行时层：SubAgentTool 构造注入 `spawn_fn: Callable | None`，子 agent 构建时传入 `spawn_fn=None`，execute 时检查后返回错误字符串作为兜底。与 `BuiltinTool._require_session()` 守卫模式一致。

### Claude's Discretion
- spawn_fn 闭包的具体签名和返回值设计（在 spawn_fn 注入解耦原则下自由实现）
- SubAgentTool 的 description/json_schema 精细化（遵循 Phase 10 确立的 Claude Code 质量标准）
- stop_event 级联传播的具体实现（共享 Event 对象 vs 子 Event 联动）
- `normalize_event_source` 的前缀解析规则细节
- `chat_history.py` source 判断的兼容改法
- 子 exp TOML 的 max_turns 和 guards 配置值
- Phase 11 交付哪些子 exp TOML 文件（最少一个用于验证机制）

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SUBA-01 | Agent 可以通过 SubAgent tool 调用 spawn 子 agent 执行特定任务，结果作为 tool_call result 返回 | SubAgentTool._execute() 调用 spawn_fn 闭包，闭包内创建子 Exp + build_runtime + kernel.run，KernelResult.final_content 作为 tool result 返回 |
| SUBA-02 | 子 agent 通过 ExpConfig 配置独立的 tool 集和 system prompt | 子 exp TOML 文件定义独立的 tools.builtin 列表和 developer_instructions，通过 load_exp_config(name) 加载 |
| SUBA-03 | 子 agent 共享父 agent 的 PlaygroundContext（workspace/session） | spawn_fn 闭包捕获父 ctx，传递给子 Exp.build_runtime(ctx)，子 agent 复用同一 session/workdir |
| SUBA-04 | 子 agent 禁止再次 spawn 子 agent（递归深度保护） | 双层保护：子 exp TOML 不含 sub_agent + spawn_fn=None 运行时守卫 |
| SUBA-05 | 父 agent 取消时 stop_event 级联传播到子 agent | 共享同一 threading.Event 对象，父 stop_event 直接传递给子 kernel.run() |
| SUBA-06 | 子 agent 的事件通过父 agent 的 MessageBus 路由到前端 | 子 agent 的 EventEmitterHook 使用父 bus + 带前缀 source，两处 normalize 函数扩展前缀规则 |
| PRMT-03 | SubAgent 的 exp 定义包含针对子任务场景的专用 system prompt | 子 exp TOML 的 developer_instructions 字段，针对子任务场景设计 |
</phase_requirements>

## Architecture Patterns

### Recommended Implementation Structure

```
matmaster/
├── tools/builtin/
│   ├── sub_agent_tool.py      # NEW: SubAgentTool class
│   └── __init__.py            # UPDATE: export SubAgentTool
├── exps/
│   ├── direct.toml            # UPDATE: tools.builtin 加入 "sub_agent"
│   └── explore.toml           # NEW: 子 exp TOML (至少一个)
├── core/
│   └── exp.py                 # UPDATE: _init_builtin_tools 注册 SubAgentTool + spawn_fn 闭包
├── integration/
│   └── event_payloads.py      # UPDATE: _normalize_public_source 扩展前缀
src/
├── utils/
│   └── chat_event_source.py   # UPDATE: normalize_event_source 扩展前缀
├── services/
│   └── chat_history.py        # UPDATE: source 判断兼容 MatMaster:* 前缀
tests/matmaster/
├── tools/
│   └── test_sub_agent_tool.py # NEW: SubAgentTool 单元测试
├── core/
│   └── test_exp.py            # UPDATE: 新增 SubAgentTool 注册测试
└── integration/
    └── test_subagent_event_routing.py  # NEW: 事件路由集成测试
```

### Pattern 1: spawn_fn 闭包注入

**What:** SubAgentTool 不直接依赖 Exp 类。Exp.build_runtime() 创建一个闭包 spawn_fn，捕获 ctx/bus/stop_event 等运行时上下文，SubAgentTool 构造时注入此闭包。

**When to use:** 当 tool 需要访问 Exp 层能力但不应直接依赖 Exp 时。

**Example:**
```python
# In Exp.build_runtime() -- spawn_fn closure creation
def _make_spawn_fn(
    ctx: PlaygroundContext,
    bus: MessageBus | None,
    stop_event: threading.Event | None,
) -> Callable[[str, str], str]:
    """Create spawn_fn closure capturing runtime context.

    Args:
        ctx: Parent PlaygroundContext (shared workspace/session).
        bus: Parent MessageBus (shared for event routing).
        stop_event: Parent stop_event (shared for cancel propagation).

    Returns:
        Callable[[exp_name, task], result_str] for SubAgentTool.
    """
    def spawn_fn(exp_name: str, task: str) -> str:
        from matmaster.config.loader import load_exp_config

        child_config = load_exp_config(exp_name)
        child_exp = Exp(child_config)
        # build_runtime with parent ctx/bus -- child shares workspace/session
        child_runtime = child_exp.build_runtime(ctx, bus=bus)
        try:
            run_result = child_runtime.kernel.run(
                child_runtime.spec, task, stop_event=stop_event
            )
            result = run_result.result
            if result.status == "completed" and result.final_content:
                return result.final_content
            return f"SubAgent finished with status={result.status}, reason={result.reason}"
        finally:
            child_runtime.cleanup()

    return spawn_fn
```

**Design rationale:** 闭包签名 `(exp_name: str, task: str) -> str` 与 Tool Protocol 的 `execute(arguments) -> str` 对齐。SubAgentTool._execute() 解析 arguments，调用 spawn_fn，返回结果字符串。

### Pattern 2: BuiltinTool 构造注入扩展

**What:** SubAgentTool 继承 BuiltinTool，扩展 __init__ 接受 spawn_fn 参数。

**Example:**
```python
class SubAgentTool(BuiltinTool):
    name: ClassVar[str] = "sub_agent"
    description: ClassVar[str] = "..."  # Claude Code 质量级别
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "exp_name": {
                "type": "string",
                "description": "Name of the sub-agent type to spawn",
            },
            "task": {
                "type": "string",
                "description": "Task description for the sub-agent",
            },
        },
        "required": ["exp_name", "task"],
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Path | None = None,
        spawn_fn: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._spawn_fn = spawn_fn

    def _execute(self, arguments: dict[str, Any]) -> str:
        if self._spawn_fn is None:
            return "Error: SubAgent spawning is not available in this context (recursion depth limit reached)"
        exp_name = arguments.get("exp_name", "")
        task = arguments.get("task", "")
        if not exp_name or not task:
            return "Error: Both exp_name and task are required"
        return self._spawn_fn(exp_name, task)
```

### Pattern 3: Source 前缀事件路由

**What:** 子 agent 的 EventEmitterHook 使用带冒号前缀的 source（如 `MatMaster:explore`），两处 normalize 函数扩展规则保留前缀中的 MatMaster 部分。

**Example:**
```python
# src/utils/chat_event_source.py
def normalize_event_source(source: Any) -> str:
    """Collapse event sources into the stable public set.

    Preserves MatMaster:* prefix format for sub-agent source distinction.
    """
    raw = str(source or '').strip()
    if raw == USER_SOURCE:
        return USER_SOURCE
    if raw == SYSTEM_SOURCE:
        return SYSTEM_SOURCE
    # Preserve MatMaster:subtype prefix for sub-agent events
    if raw.startswith('MatMaster:'):
        return raw
    return MATMASTER_SOURCE
```

```python
# matmaster/integration/event_payloads.py
def _normalize_public_source(source: object) -> str:
    """Collapse internal source labels to the public SSE set."""
    raw = str(source or "").strip()
    if raw in {"User", "System"}:
        return raw
    # Preserve MatMaster:subtype prefix for sub-agent events
    if raw.startswith("MatMaster:"):
        return raw
    return "MatMaster"
```

### Pattern 4: chat_history.py source 判断兼容

**What:** `chat_history.py` 中的 source 判断使用精确匹配 `== 'MatMaster'`，需要改为前缀匹配以兼容子 agent 事件。

**Example:**
```python
# chat_history.py -- 将 source == 'MatMaster' 改为辅助函数
def _is_matmaster_source(source: str) -> bool:
    """Check if source is MatMaster or MatMaster:subtype."""
    return source == 'MatMaster' or source.startswith('MatMaster:')
```

**Critical spots in chat_history.py that need updating:**
1. Line 271: `if source == 'MatMaster' and typ in ('thought', 'planner_reply'):`
2. Line 281: `if source == 'MatMaster' and typ == 'response':`
3. Line 292: `if source == 'MatMaster' and typ == 'assistant_state':`
4. Line 344: `if source == 'MatMaster' and typ in ('run_result', 'finish'):`

All four need改为 `if _is_matmaster_source(source) and ...`

### Anti-Patterns to Avoid
- **SubAgentTool 直接 import Exp:** 破坏层级边界。Tool 层不应依赖 Exp 层，spawn_fn 闭包是正确的解耦方式。
- **子 agent 使用独立 MessageBus:** 会导致子 agent 事件无法被父 agent 的 EventRouter 消费，前端看不到子 agent 执行过程。
- **子 agent 创建独立 stop_event:** 会导致父取消时子 agent 无法感知，违反 SUBA-05。
- **在 spawn_fn 中传递 history:** 子 agent 是独立任务，不应继承父 agent 的对话历史。task 参数已包含必要上下文。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 子 exp 配置加载 | 自定义配置解析 | `load_exp_config(name)` | 已有完整的 TOML 加载链路，支持 env-var 展开和 Pydantic 验证 |
| 子 agent 执行循环 | 简化版 agent loop | `AgentKernel.run(spec, task)` | 复用同一 Kernel，保证 guard/hook/tool 行为一致 |
| 子 agent system prompt | 手动拼接 prompt | `ContextBuilder.build()` via `Exp.build_runtime()` | 保证 section order 和 tool listing 与父 agent 一致 |
| 事件发射 | 手动 bus.emit() | `EventEmitterHook(bus, source=prefixed_name)` | 复用 hook 机制，自动处理 streaming/tool_call/tool_result |
| Tool 注册 | 手动插入 registry | `registry.register(tool, source="builtin")` in `_init_builtin_tools` | 保持 source 标签和 override 语义一致 |

**Key insight:** Phase 11 的核心价值在于 spawn 机制的连接胶水（闭包注入、事件路由），而非重新实现 agent 执行链路。所有底层组件（Kernel、Exp、ToolRegistry、EventEmitterHook）均已完备，只需正确组装。

## Common Pitfalls

### Pitfall 1: spawn_fn 闭包生命周期

**What goes wrong:** spawn_fn 闭包捕获的 ctx/bus 在 SubAgentTool.execute() 调用时已被清理或失效。
**Why it happens:** Exp.build_runtime() 返回 AgentRuntime 后，如果闭包中的引用被 GC 或清理回调提前释放。
**How to avoid:** spawn_fn 应捕获 build_runtime 创建时的 ctx（frozen，不会变）和 bus（生命周期由调用方管理，run 期间一直存活）。不要在闭包中捕获可变状态。
**Warning signs:** SubAgentTool.execute() 抛出 AttributeError 或访问已 cleanup 的资源。

### Pitfall 2: 子 agent cleanup 遗漏

**What goes wrong:** spawn_fn 中子 Exp 创建的资源（ReadTracker 等）未被清理，累积泄漏。
**Why it happens:** spawn_fn 内部创建子 Exp 和 child_runtime，但没有在 finally 中调用 child_runtime.cleanup()。
**How to avoid:** spawn_fn 必须在 try/finally 中调用 child_runtime.cleanup()，与 Exp.run() 的模式一致。
**Warning signs:** 多次 SubAgent 调用后 ReadTracker 状态不一致，或者 MCP 连接泄漏（虽然子 exp 不用 MCP）。

### Pitfall 3: source 前缀改动影响面

**What goes wrong:** 修改 normalize_event_source 后，chat_history.py 中的精确匹配 `source == 'MatMaster'` 不匹配子 agent 事件，导致子 agent 的对话历史丢失。
**Why it happens:** normalize_event_source 改为保留 `MatMaster:explore` 后，chat_history 中 `source == 'MatMaster'` 不再匹配。
**How to avoid:** 同步更新 chat_history.py 中所有 4 处 source 判断，以及 normalize_event_source 的所有消费者。
**Warning signs:** 多轮对话中子 agent 的 thought/response/tool_call 事件不出现在历史中。

### Pitfall 4: 子 agent tool_call 工具集不一致

**What goes wrong:** 子 agent 配置了 `tools.builtin = ["read_file", "write_file"]` 但 `_init_builtin_tools()` 注册了所有 12 个 native tools。
**Why it happens:** 当前 `_init_builtin_tools()` 不检查 `builtin` 列表的具体内容，只检查是否为空列表来决定是否执行。一旦列表非空，注册所有 12 个 native tools + 1 个 evo adapter。
**How to avoid:** Phase 11 可以利用这个行为：子 exp TOML 的 tools.builtin 列表控制 LLM 可见的工具（通过 ToolRegistry 的 filter 或注册时过滤），或者接受当前行为（子 agent 获得与父 agent 相同的工具集，只是 system prompt 不同）。**推荐接受当前行为**——子 exp 的 tools.builtin 列表非空即触发全量注册，tool 选择通过 system prompt 引导 LLM 行为。这与 D-03 的 developer_instructions 引导策略一致。
**Warning signs:** 子 agent 使用了不应使用的工具。

### Pitfall 5: 子 agent 同步执行阻塞父 agent

**What goes wrong:** 子 agent 执行时间过长，父 agent 的 tool execution 线程被阻塞。
**Why it happens:** SubAgentTool.execute() 同步调用 spawn_fn，spawn_fn 内部调用 kernel.run() 是同步阻塞的。
**How to avoid:** 子 exp TOML 应设置合理的 max_turns（建议 30-50，远低于父 agent 的 200），并设置适当的 guards。stop_event 级联传播确保可取消。
**Warning signs:** 整体响应时间过长，前端超时。

## Code Examples

### SubAgentTool 注册进 _init_builtin_tools

```python
# In Exp._init_builtin_tools() -- after existing native_tools list
# Source: matmaster/core/exp.py

from matmaster.tools.builtin.sub_agent_tool import SubAgentTool

# SubAgentTool: spawn_fn injected separately (not part of standard native_tools)
# spawn_fn will be set after _init_builtin_tools if "sub_agent" is in config
sub_agent_tool = SubAgentTool(
    session=ctx.session,
    workdir=ctx.workdir,
    spawn_fn=None,  # Set later in build_runtime via _make_spawn_fn
)
registry.register(sub_agent_tool, source="builtin")
```

**Note:** spawn_fn 需要在 build_runtime 中设置，因为它依赖 bus 等在 _init_builtin_tools 之后才创建的资源。有两种实现方式：

**(A) 先注册 spawn_fn=None，后通过属性设置:**
```python
# In build_runtime(), after _init_builtin_tools and hooks creation:
sub_agent_tools = [
    t for t in registry.all_tools
    if isinstance(t, SubAgentTool)
]
if sub_agent_tools:
    spawn_fn = self._make_spawn_fn(ctx, bus, stop_event=None)
    for t in sub_agent_tools:
        t._spawn_fn = spawn_fn
```

**(B) 在 build_runtime 中注册 SubAgentTool（推荐）:**
```python
# In build_runtime(), after creating EventEmitterHook:
if "sub_agent" in self._config.tools.builtin or self._config.tools.builtin == ["*"]:
    from matmaster.tools.builtin.sub_agent_tool import SubAgentTool
    spawn_fn = self._make_spawn_fn(ctx, bus)
    sub_tool = SubAgentTool(
        session=ctx.session, workdir=ctx.workdir, spawn_fn=spawn_fn
    )
    registry.register(sub_tool, source="builtin")
```

**推荐方案 B:** spawn_fn 在构造时即确定，避免后续修改 frozen 可能的问题。但需要注意注册时机在 ContextBuilder.build() 之前，以确保 sub_agent 出现在 system prompt 的 Available Tools 列表中。

### 子 exp TOML 示例 (explore.toml)

```toml
# matmaster/exps/explore.toml
name = "explore"
mode = "direct"
max_turns = 50
guards = []

developer_instructions = '''
You are an exploration sub-agent for Mat Master. Your task is to investigate
code, data files, and project structure on behalf of the parent agent.

# Capabilities
- Read and search files to understand code structure and data formats
- Execute commands to inspect environments and dependencies
- Report findings concisely for the parent agent to act on

# Constraints
- Do NOT modify any files. You are read-only.
- Do NOT start long-running computations
- Focus on gathering information, not taking actions
- Be concise -- your output is consumed by another agent, not a human
'''

mode_contract = '''
You are in direct execution mode as an exploration sub-agent.
Gather the requested information and return a concise summary.
Do not ask clarifying questions -- work with what you have.
'''

[tools]
builtin = [
    "execute_bash",
    "list_dir",
    "read_file",
    "glob",
    "grep",
]
mcp = ""

[skills]
enabled = false
```

### stop_event 级联传播

```python
# In spawn_fn -- direct sharing of parent stop_event
# Source: threading.Event is thread-safe by design

def spawn_fn(exp_name: str, task: str) -> str:
    child_config = load_exp_config(exp_name)
    child_exp = Exp(child_config)
    child_runtime = child_exp.build_runtime(ctx, bus=bus)
    try:
        # pass parent stop_event directly -- threading.Event is thread-safe
        # when parent sets stop_event, child kernel's cancel check fires immediately
        run_result = child_runtime.kernel.run(
            child_runtime.spec, task, stop_event=stop_event
        )
        ...
    finally:
        child_runtime.cleanup()
```

**Why direct sharing works:** `threading.Event.is_set()` 是线程安全的。AgentKernel.run() 在每个 turn 开始时检查 `if stop_event and stop_event.is_set()`。父子 agent 在同一线程中同步执行（子 agent 在父 agent 的 tool execution 中运行），所以共享同一 Event 对象即可。当 stop_event 从外部（service layer / cancel API）被 set 时，如果子 agent 正在运行的 LLM 调用返回后，下一轮 turn 开始时会检测到 cancelled。

### EventEmitterHook source 前缀

```python
# In spawn_fn -- child EventEmitterHook uses prefixed source
# Source: matmaster/core/hooks.py EventEmitterHook.__init__(bus, source)

child_runtime = child_exp.build_runtime(ctx, bus=bus)
# build_runtime internally creates EventEmitterHook(bus, source=child_exp.exp_name)
# child_exp.exp_name = "explore" (from TOML name field)
# BUT: we need source = "MatMaster:explore", not just "explore"
```

**Issue:** `Exp.build_runtime()` 创建 EventEmitterHook 时使用 `source=self.exp_name`（第 134 行），对于子 exp 这会是 "explore" 而非 "MatMaster:explore"。

**Solution options:**
1. build_runtime 增加 `source_prefix` 参数
2. spawn_fn 在调用 build_runtime 后替换 hook 的 source
3. 在 spawn_fn 中手动创建 EventEmitterHook 并注入

**推荐方案 1:** 最小改动，保持 build_runtime 的灵活性：
```python
def build_runtime(
    self, ctx, *, bus=None, skills=None, mcp=None,
    source_override: str | None = None,
) -> AgentRuntime:
    ...
    if bus is not None:
        emitter_source = source_override or self.exp_name
        emitter_hook = EventEmitterHook(bus, source=emitter_source)
        hooks.append(emitter_hook)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| 直接 import Exp 在 Tool 中 | 闭包注入 spawn_fn | Phase 11 设计 | Tool 层不依赖 Exp 层，保持层级边界 |
| 所有事件 source = exp_name | source 支持前缀格式 | Phase 11 设计 | 前端可区分父/子 agent 事件 |
| ExpConfig.tools.builtin 控制注册 | tools.builtin 非空即全量注册 | Phase 9 | 子 exp 的 tool 选择主要靠 prompt 引导 |

## Open Questions

1. **spawn_fn 中 stop_event 的传递时机**
   - What we know: spawn_fn 闭包在 build_runtime 时创建，但 stop_event 是在 Exp.run() 的参数中传入。当前 build_runtime 没有 stop_event 参数。
   - What's unclear: spawn_fn 需要访问 stop_event，但 stop_event 在 build_runtime 之后才可用（run 时传入）。
   - Recommendation: 两种解决方案：(A) 将 stop_event 作为 spawn_fn 的额外参数而非闭包捕获 `spawn_fn(exp_name, task, stop_event)`，由 Exp.run() 在调用 kernel.run 前设置；(B) 在 _init_builtin_tools 中延迟设置 spawn_fn，在 run() 中完成闭包创建。**推荐方案 A**：签名变为 `(exp_name: str, task: str, stop_event: threading.Event | None) -> str`，SubAgentTool 在 execute 时从调用上下文获取 stop_event。**但更简洁的方案**：直接让 spawn_fn 闭包不捕获 stop_event，而是让 SubAgentTool._execute() 不传 stop_event（子 agent 独立运行），或者通过 BuiltinTool 基类扩展传递 kernel 的 stop_event。需要在 plan 中明确。

2. **source_override 参数对 build_runtime 签名的影响**
   - What we know: 当前 build_runtime 签名为 `(ctx, *, bus, skills, mcp)`。
   - What's unclear: 增加 source_override 参数是否会影响现有调用方（agent_run_service.py）。
   - Recommendation: source_override 默认 None，现有调用方无需修改。仅 spawn_fn 内部调用时传入 `source_override=f"MatMaster:{exp_name}"`。

3. **SubAgentTool 接收 stop_event 的机制**
   - What we know: Tool Protocol 的 execute 签名是 `execute(arguments: dict[str, Any]) -> str`，没有 stop_event 参数。BuiltinTool.__init__ 也没有 stop_event。
   - What's unclear: SubAgentTool 如何获取父 agent 的 stop_event。
   - Recommendation: **最佳方案**：spawn_fn 闭包签名为 `(exp_name, task) -> str`，stop_event 通过闭包捕获。但闭包创建时机需要调整：不在 build_runtime 中创建，而是在 run() 中创建（此时 stop_event 已可用）。或者：在 build_runtime 中创建不含 stop_event 的闭包，run() 调用前通过 SubAgentTool 的属性设置 stop_event。**推荐最终方案**：闭包中直接捕获 stop_event=None，在 Exp.run() 中（kernel.run 之前）通过 SubAgentTool._stop_event = stop_event 注入。这与 ReadTracker 的 cleanup 注册模式一致。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (via uv run pytest) |
| Config file | pytest.ini |
| Quick run command | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py -x` |
| Full suite command | `uv run pytest tests/matmaster/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SUBA-01 | SubAgentTool execute -> spawn_fn -> result string | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py::test_execute_calls_spawn_fn -x` | Wave 0 |
| SUBA-02 | 子 exp TOML 加载独立 tools/prompt | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py::test_child_exp_config -x` | Wave 0 |
| SUBA-03 | 共享 PlaygroundContext | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py::test_shared_context -x` | Wave 0 |
| SUBA-04 | 递归保护 -- spawn_fn=None 返回错误 | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py::test_recursion_guard -x` | Wave 0 |
| SUBA-05 | stop_event 级联传播 | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py::test_stop_event_propagation -x` | Wave 0 |
| SUBA-06 | 子 agent 事件通过父 bus 路由 | integration | `uv run pytest tests/matmaster/integration/test_subagent_event_routing.py -x` | Wave 0 |
| PRMT-03 | 子 exp TOML 含专用 prompt | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py::test_child_exp_prompt -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py -x`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/tools/test_sub_agent_tool.py` -- SubAgentTool 单元测试（SUBA-01, SUBA-02, SUBA-03, SUBA-04, SUBA-05, PRMT-03）
- [ ] `tests/matmaster/integration/test_subagent_event_routing.py` -- 事件路由集成测试（SUBA-06）
- [ ] `tests/test_chat_event_source.py` -- 需新增 MatMaster:prefix 测试用例

*(Existing test infrastructure for pytest is fully operational -- 870 tests collected, framework and conftest fixtures available)*

## Project Constraints (from CLAUDE.md)

- Python 环境始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 规范：全部放文件顶部，按 标准库 - 第三方 - 本地 分组
- 单文件行数超过 1000 行需重构
- 异常处理：DAO 层不吞异常；service 层按需降级

## Sources

### Primary (HIGH confidence)
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC, _require_session 守卫模式
- `matmaster/core/exp.py` -- Exp.build_runtime(), _init_builtin_tools() 注册模式
- `matmaster/core/agent.py` -- AgentKernel.run() 执行循环, stop_event 检查
- `matmaster/core/bus.py` -- MessageBus 线程安全 queue.Queue
- `matmaster/core/hooks.py` -- EventEmitterHook(bus, source) 构造模式
- `matmaster/config/loader.py` -- load_exp_config(name) TOML 加载链路
- `matmaster/exps/direct.toml` -- 父 exp TOML 结构参照
- `src/utils/chat_event_source.py` -- normalize_event_source 当前实现
- `matmaster/integration/event_payloads.py` -- _normalize_public_source 当前实现
- `src/services/chat_history.py` -- source == 'MatMaster' 判断位置
- `src/services/agent_run_service.py` -- service 层 Exp 使用模式

### Secondary (MEDIUM confidence)
- Phase 8/9/10 CONTEXT.md -- 前驱 phase 设计决策

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 无新依赖，全部复用现有组件
- Architecture: HIGH - 所有代码均已直接阅读，模式清晰
- Pitfalls: HIGH - 基于实际代码审查发现的具体问题点
- Open questions: MEDIUM - stop_event 传递机制有三种方案需在 plan 中决定

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable internal architecture, no external dependency changes)
