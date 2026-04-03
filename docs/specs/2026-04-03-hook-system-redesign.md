# Hook System Redesign

## 背景

matmaster 现有 hook 系统（`matmaster/core/hooks.py`）存在以下问题：

1. **接线断裂** — Phase 34 引入 FullToolRunner 后，`pre_tool_call`、`post_tool_call`、`on_guard_blocked` 三个工具级 hook 未接入新执行路径，仅 `pre_llm_call` 和 `should_continue` 在 AgentKernel 中工作
2. **职责混杂** — 7 个 hook 点混合了工具执行控制、LLM 循环控制、流式输出观察三个关注面，粒度不统一
3. **缺少关键事件** — 无会话生命周期、subagent 启停、上下文压缩、用户输入提交等事件
4. **无执行策略区分** — 所有 hook 串行执行，观察型 hook 不必要地阻塞主流程

本设计参考 Claude Code 的 hook 体系（统一分发、并行执行、结果聚合），结合 matmaster 纯内部 Python 消费的场景进行适配。

## 设计决策记录

| 决策 | 结论 | 理由 |
|------|------|------|
| 消费者 | 纯内部 Python callback | hook 只服务 matmaster 自身子系统，无需配置文件驱动或插件生态 |
| 执行模型 | Claude Code 统一分发，观察/拦截并行，改写串行 | 观察和拦截 hook 之间无因果依赖，并行提高吞吐；改写 hook 需要链式叠加 |
| 错误处理 | 静默吞掉 + 日志 | hook 是扩展点，不应成为系统单点故障 |
| 超时 | 不需要 | 纯内部 callback，执行时间可控，由 hook 实现者自行负责 |

## 事件模型

8 个 hook 事件，按能力分为三类：

| 事件 | 能力 | 执行策略 |
|------|------|---------|
| `SESSION_START` | 观察 | 并行 |
| `SESSION_END` | 观察 | 并行 |
| `PRE_TOOL_CALL` | 观察 + 拦截 | 并行，聚合结果 |
| `POST_TOOL_CALL` | 观察 + 改写 | 观察并行，改写串行 |
| `SUBAGENT_START` | 观察 | 并行 |
| `SUBAGENT_STOP` | 观察 | 并行 |
| `CONTEXT_COMPACTION` | 观察 | 并行 |
| `USER_PROMPT_SUBMIT` | 观察 + 改写 | 观察并行，改写串行 |

```python
class HookEvent(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    CONTEXT_COMPACTION = "context_compaction"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
```

## 事件上下文

每个事件携带 frozen dataclass 上下文，hook 不能意外篡改入参，改写通过返回值实现：

```python
@dataclass(frozen=True)
class SessionContext:
    session_id: str
    reason: str  # "startup" | "resume" | "cancelled" | "completed" | "max_turns" | "error"

@dataclass(frozen=True)
class PreToolCallContext:
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    turn: int

@dataclass(frozen=True)
class PostToolCallContext:
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    result: ToolResult
    turn: int

@dataclass(frozen=True)
class SubagentContext:
    agent_id: str
    agent_type: str
    parent_session_id: str

@dataclass(frozen=True)
class CompactionContext:
    messages_before: int
    messages_after: int
    tokens_saved: int

@dataclass(frozen=True)
class UserPromptContext:
    prompt: str
    session_id: str
```

## 返回值模型

```python
class HookOutcome(str, Enum):
    SUCCESS = "success"
    BLOCK = "block"    # 仅 PRE_TOOL_CALL 有意义
    ERROR = "error"    # 非阻断错误，记日志继续

@dataclass
class HookResult:
    outcome: HookOutcome = HookOutcome.SUCCESS
    message: str = ""   # 人类可读原因（日志/调试用）
    data: Any = None    # 改写型 hook 放修改后的数据
```

## Handler 签名

三种 handler 对应三种能力：

```python
# 观察型：无返回值要求
ObserveHandler = Callable[[TContext], Awaitable[None]]

# 拦截型：返回 HookResult，outcome=BLOCK 阻断执行
InterceptHandler = Callable[[TContext], Awaitable[HookResult]]

# 改写型：接收 (context, current_data)，返回修改后的数据或 None（None 表示不修改）
RewriteHandler = Callable[[TContext, T], Awaitable[T | None]]
```

使用示例：

```python
# 观察：会话开始时记日志
async def log_session_start(ctx: SessionContext) -> None:
    logger.info(f"Session {ctx.session_id} started: {ctx.reason}")

# 拦截：禁止执行危险命令
async def block_dangerous_bash(ctx: PreToolCallContext) -> HookResult:
    if ctx.tool_name == "execute_bash" and "rm -rf" in ctx.arguments.get("command", ""):
        return HookResult(outcome=HookOutcome.BLOCK, message="dangerous command blocked")
    return HookResult(outcome=HookOutcome.SUCCESS)

# 改写：给工具结果追加元信息
async def enrich_tool_result(ctx: PostToolCallContext, result: ToolResult) -> ToolResult | None:
    if ctx.tool_name == "web_fetch":
        return result.copy(update={"meta": {**result.meta, "fetched_at": time.time()}})
    return None  # 不修改
```

## HookExecutor 核心分发器

```python
class HookExecutor:
    def __init__(self) -> None:
        self._observers: dict[HookEvent, list[ObserveHandler]] = defaultdict(list)
        self._interceptors: dict[HookEvent, list[InterceptHandler]] = defaultdict(list)
        self._rewriters: dict[HookEvent, list[RewriteHandler]] = defaultdict(list)

    # ── 注册 ──
    def on(self, event: HookEvent, handler: ObserveHandler) -> None: ...
    def intercept(self, event: HookEvent, handler: InterceptHandler) -> None: ...
    def rewrite(self, event: HookEvent, handler: RewriteHandler) -> None: ...

    # ── 分发 ──
    async def emit(self, event: HookEvent, ctx: Any) -> None:
        """观察型分发：asyncio.gather 并行，异常吞掉记日志"""

    async def emit_intercept(self, event: HookEvent, ctx: Any) -> HookResult:
        """拦截型分发：并行执行所有 interceptor，聚合结果。
        任一返回 BLOCK -> 最终 BLOCK；全部 SUCCESS -> SUCCESS"""

    async def emit_rewrite(self, event: HookEvent, ctx: Any, data: T) -> T:
        """改写型分发：串行链式，每个 rewriter 拿到上一个的输出。
        返回 None 表示不修改，透传当前值给下一个"""
```

### 分发逻辑

```python
async def emit(self, event: HookEvent, ctx: Any) -> None:
    handlers = self._observers.get(event, [])
    if not handlers:
        return
    results = await asyncio.gather(
        *(self._safe_call(h, ctx) for h in handlers),
        return_exceptions=True,
    )
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            logger.warning("Hook %s raised: %s", handlers[i], r)

async def emit_intercept(self, event: HookEvent, ctx: Any) -> HookResult:
    handlers = self._interceptors.get(event, [])
    if not handlers:
        return HookResult()
    results = await asyncio.gather(
        *(self._safe_intercept(h, ctx) for h in handlers),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, HookResult) and r.outcome == HookOutcome.BLOCK:
            return r
    return HookResult()

async def emit_rewrite(self, event: HookEvent, ctx: Any, data: T) -> T:
    for handler in self._rewriters.get(event, []):
        try:
            modified = await handler(ctx, data)
            if modified is not None:
                data = modified
        except Exception as e:
            logger.warning("Rewrite hook %s raised: %s", handler, e)
    return data

async def _safe_call(self, handler, ctx):
    try:
        await handler(ctx)
    except Exception as e:
        raise  # 让 gather 捕获

async def _safe_intercept(self, handler, ctx) -> HookResult:
    try:
        return await handler(ctx)
    except Exception as e:
        logger.warning("Intercept hook %s raised: %s", handler, e)
        return HookResult(outcome=HookOutcome.ERROR, message=str(e))
```

## 接线方案

HookExecutor 挂在 AgentRuntimeSpec 上，取代现有 `hooks: list[Hook]`：

```python
# matmaster/types/runtime.py
@dataclass
class AgentRuntimeSpec:
    ...
    hook_executor: HookExecutor | None = None  # 替代 hooks: list[Hook]
```

### 注入位置

```
agent_run_service.run_agent()
|
+- Exp.build_runtime() 完成后
|  +- emit(SESSION_START, SessionContext(reason="startup"))
|
+- AgentKernel._run_items() 循环:
|  |
|  +- 用户消息进入前 (stream_service)
|  |  +- emit(USER_PROMPT_SUBMIT, ctx)
|  |  +- prompt = emit_rewrite(USER_PROMPT_SUBMIT, ctx, prompt)
|  |
|  +- FullToolRunner.execute_batch() Phase 1 串行校验末尾:
|  |  +- emit(PRE_TOOL_CALL, ctx)
|  |  +- result = emit_intercept(PRE_TOOL_CALL, ctx)
|  |     +- BLOCK -> ToolResult(status="blocked"), 跳过 Phase 2
|  |
|  +- FullToolRunner Phase 2 完成后:
|  |  +- emit(POST_TOOL_CALL, ctx)
|  |  +- result = emit_rewrite(POST_TOOL_CALL, ctx, result)
|  |
|  +- SpawnTool.execute() 前后:
|  |  +- emit(SUBAGENT_START, ctx)
|  |  +- emit(SUBAGENT_STOP, ctx)
|  |
|  +- ContextCompactor.compact_if_needed() 完成后:
|     +- emit(CONTEXT_COMPACTION, ctx)
|
+- finally 块, cleanup 前:
   +- emit(SESSION_END, SessionContext(reason=终止原因))
```

### 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `matmaster/core/hooks.py` | **重写**：删除旧 Hook Protocol / BaseHook / run_* helpers，放入 HookEvent、context dataclasses、HookOutcome、HookResult、handler 类型别名、HookExecutor |
| `matmaster/types/runtime.py` | `AgentRuntimeSpec.hooks: list[Hook]` -> `hook_executor: HookExecutor \| None` |
| `matmaster/core/exp.py` | `build_runtime()` 中创建 HookExecutor 实例并注入 spec |
| `matmaster/core/tool_runner.py` | FullToolRunner Phase 1 末尾加 pre_tool hook 调用，Phase 2 后加 post_tool hook 调用 |
| `matmaster/core/agent.py` | `_run_items` 中压缩完成后加 compaction hook 调用；删除旧 `run_pre_llm_call` / `run_should_continue` 调用 |
| `matmaster/tools/builtin/spawn_tool.py` | execute 前后加 subagent start/stop hook 调用 |
| `src/services/agent_run_service.py` | run_agent 入口加 session_start，finally 加 session_end |
| `src/services/stream_service.py` | 用户消息进入时加 user_prompt_submit hook 调用 |

### DevStreamHook 迁移

DevStreamHook 当前实现的是流式输出观察（on_stream_chunk、on_event），不在本次 hook 事件范围内。保持现有直接回调方式，不阻塞本次设计。后续如需纳入可新增 `STREAM_CHUNK` 事件。

## 删除清单

| 删除内容 | 位置 |
|---------|------|
| `Hook` Protocol | `matmaster/core/hooks.py` |
| `HookAction` Enum | `matmaster/core/hooks.py` |
| `BaseHook` 类 | `matmaster/core/hooks.py` |
| `run_pre_tool_call()` 等 6 个 helper 函数 | `matmaster/core/hooks.py` |
| `AgentRuntimeSpec.hooks: list[Hook]` 字段 | `matmaster/types/runtime.py` |
| `InlineToolRunner` 中的 hook 调用 | `matmaster/core/tool_runner.py`（如果 InlineToolRunner 仍存在） |
| `matmaster/hooks/__init__.py` | 空目录，已无用 |

## 不在范围内

- 配置文件驱动的 hook 注册（当前只需代码注册）
- hook 超时机制（内部 callback 自行负责）
- matcher 模式匹配（Claude Code 的多来源匹配，当前不需要）
- 流式 chunk 观察（保持现有 DevStreamHook 直接回调）
- LLM 调用前后观察（不在用户需求中）
- should_continue 循环控制（不在用户需求中）
