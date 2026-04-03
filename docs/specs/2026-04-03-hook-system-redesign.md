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
| 注册时机 | build_runtime 阶段一次性注册，运行时不再修改 | HookExecutor 内部持有可变字典，但 frozen AgentRuntimeSpec 只阻止字段重新赋值。注册完成后不再调用 on/intercept/rewrite，靠协议保证而非类型强制 |
| 旧 hook 删除安全性 | `pre_llm_call` 和 `should_continue` 可安全删除 | 当前唯一的 BaseHook 子类是 DevStreamHook，其 `pre_llm_call` 为空操作（继承 BaseHook 默认实现），`should_continue` 无活跃实现。删除调用点不影响运行时行为 |
| 废除 D-01 约束 | FullToolRunner 重新接入 hook 调用 | D-01 约束（"Does not call pre_hook/post_hook"）是旧 Hook Protocol 时代的决策，因 FullToolRunner 采用了新的分层校验架构而跳过了旧 hook。本次重新设计 hook 系统后，HookExecutor 的调用点明确插入 Phase 1 catalog lookup 后、validation 前，以及 Phase 2 之后 |
| 接线层级 | hook 调用点位于 AgentKernel.run_stream / _run_items / FullToolRunner 内部 | agent_run_service 和 devshell/runner.py 走不同路径，但都最终调用 `kernel.run_stream()`。RUN_START/END 放在 kernel.run_stream 确保三条路径（agent_run_service 经 Exp.run_stream、devshell 经 build_runtime + kernel.run_stream、spawn 子 agent）统一覆盖 |
| 事件命名 | RUN_START/RUN_END 而非 SESSION_START/END | 每次 run_agent/run_stream 调用是一个 run，不是一个 session。一个 session 可包含多次 run |
| USER_PROMPT_SUBMIT 改写持久化 | 瞬时改写，不更新持久化历史 | prompt 在进 worker 前已被 stream_service 持久化，chat_history 从持久化事件重建 UserMessage。改写只影响当前执行，回放使用原始 prompt。持久化改写需改动 chat_history 层，当前无此场景需求 |
| PRE_TOOL_CALL 位置 | catalog lookup 之后、validation 之前 | 确保所有被尝试调用的工具（含后续被 guard/policy 拒绝的）都能被 hook 观察和拦截。若放在 Phase 1 末尾，早期 deny（structural/input/guard/policy）的工具调用对 hook 不可见 |
| SUBAGENT hook 调用点 | Exp._make_spawn_fn 闭包内部，而非 SpawnTool.execute | 闭包内可直接访问 child_spawn_id（作为 SubagentContext.agent_id）和 HookExecutor。SpawnTool.execute 只拿到最终字符串结果，无法填充 SubagentContext |

## 事件模型

8 个 hook 事件，按能力分为三类：

| 事件 | 能力 | 执行策略 |
|------|------|---------|
| `RUN_START` | 观察 | 并行 |
| `RUN_END` | 观察 | 并行 |
| `PRE_TOOL_CALL` | 观察 + 拦截 | 先 emit 观察（并行），再 emit_intercept 拦截（并行+聚合）。观察 handler 始终执行，不受拦截结果影响 |
| `POST_TOOL_CALL` | 观察 + 改写 | 先 emit_rewrite 改写（串行链），再 emit 观察（并行）。观察 handler 看到的是改写后的 result |
| `SUBAGENT_START` | 观察 | 并行 |
| `SUBAGENT_STOP` | 观察 | 并行 |
| `CONTEXT_COMPACTION` | 观察 | 并行 |
| `USER_PROMPT_SUBMIT` | 观察 + 改写 | 先 emit_rewrite 改写（串行链），再 emit 观察（并行）。观察 handler 看到的是改写后的 prompt |

```python
class HookEvent(str, Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
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
class RunContext:
    task_id: str
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
    agent_id: str             # 由 Exp._make_spawn_fn 内部的 child_spawn_id 传递
    agent_type: str           # exp_name
    parent_session_id: str
    task_preview: str = ""    # 截断的任务描述（前 200 字符）

@dataclass(frozen=True)
class CompactionContext:
    messages_before: int      # 压缩前 len(messages)
    messages_after: int       # 压缩后 len(messages)
    trigger_tokens: int       # 触发压缩时的 token 估算值，直接映射 ContextCompactionEvent.payload["trigger_tokens"]
    strategy: str             # "summary" | "sliding_window" | "tool_truncation"，映射 payload["strategy"]

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
TContext = TypeVar("TContext")
T = TypeVar("T")

# 观察型：无返回值要求
ObserveHandler = Callable[[TContext], Awaitable[None]]

# 拦截型：返回 HookResult，outcome=BLOCK 阻断执行
InterceptHandler = Callable[[TContext], Awaitable[HookResult]]

# 改写型：接收 (context, current_data)，返回修改后的数据或 None（None 表示不修改）
# None 仅作为 pass-through sentinel，表示"不修改"。当前所有改写目标类型
# （ToolResult, str）均不可能为 None，因此无歧义。若未来新增改写事件的
# data 类型本身允许 None 值，需用 dataclass 包装以区分。
RewriteHandler = Callable[[TContext, T], Awaitable[T | None]]
```

类型安全说明：HookExecutor 的注册方法不是泛型的，无法在编译期校验 handler 参数类型是否与事件的 context 类型匹配。这是有意的运行时约定——类型不匹配的 handler 会在执行时抛异常，被静默吞掉并记日志。未来可通过 `@overload` 增强。

使用示例：

```python
# 观察：运行开始时记日志
async def log_run_start(ctx: RunContext) -> None:
    logger.info(f"Run {ctx.task_id} started: {ctx.reason}")

# 拦截：禁止执行危险命令
async def block_dangerous_bash(ctx: PreToolCallContext) -> HookResult:
    if ctx.tool_name == "execute_bash" and "rm -rf" in ctx.arguments.get("command", ""):
        return HookResult(outcome=HookOutcome.BLOCK, message="dangerous command blocked")
    return HookResult(outcome=HookOutcome.SUCCESS)

# 改写：给工具结果追加元信息
async def enrich_tool_result(ctx: PostToolCallContext, result: ToolResult) -> ToolResult | None:
    if ctx.tool_name == "web_fetch":
        return result.model_copy(update={"meta": {**result.meta, "fetched_at": time.time()}})
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
        *(h(ctx) for h in handlers),
        return_exceptions=True,
    )
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            logger.warning("Hook %s raised: %s", handlers[i], r)

async def emit_intercept(self, event: HookEvent, ctx: Any) -> HookResult:
    handlers = self._interceptors.get(event, [])
    if not handlers:
        return HookResult()
    # _safe_intercept 内部已捕获所有异常，不需要 return_exceptions
    results = await asyncio.gather(
        *(self._safe_intercept(h, ctx) for h in handlers),
    )
    # 聚合所有 BLOCK 结果的 message
    blocks = [r for r in results if r.outcome == HookOutcome.BLOCK]
    if blocks:
        combined_msg = "; ".join(b.message for b in blocks if b.message)
        return HookResult(outcome=HookOutcome.BLOCK, message=combined_msg)
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

async def _safe_intercept(self, handler: InterceptHandler, ctx: Any) -> HookResult:
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

所有 hook 调用点位于 `Exp.run_stream` / kernel / FullToolRunner 边界内，确保 agent_run_service、devshell、spawn 子 agent 三条执行路径统一覆盖。

```
Exp.run_stream(task, history, stop_event) / devshell runner.py
|
+- build_runtime()
|  +- 创建 HookExecutor（在 _make_spawn_fn 之前），注入 AgentRuntimeSpec
|  +- 注入 task_id/session_id 到 spec.meta（从 ctx.run_meta 提取）
|
+- kernel.run_stream(spec, task, history, stop_event)   ← 所有路径的真正公共入口
|  |
|  +- emit(RUN_START, RunContext(task_id, session_id, reason="startup"))
|  |
|  +- AgentKernel._run_items() 循环:
|  |  |
|  |  +- 构造 UserMessage 前:
|  |  |  +- prompt = emit_rewrite(USER_PROMPT_SUBMIT, ctx, prompt)  # 改写先行
|  |  |  +- emit(USER_PROMPT_SUBMIT, ctx_with_final_prompt)         # 观察看到最终值
|  |  |
|  |  +- FullToolRunner.execute_batch() Phase 1, catalog lookup 之后、validation 之前:
|  |  |  +- emit(PRE_TOOL_CALL, ctx)                                # 观察始终执行，无论后续拦截结果
|  |  |  +- result = emit_intercept(PRE_TOOL_CALL, ctx)             # 拦截决策（观察已完成，不回滚）
|  |  |     +- BLOCK -> ToolResult(status="blocked"), 跳过后续 validation 和 Phase 2
|  |  |
|  |  +- FullToolRunner Phase 2 完成后:
|  |  |  +- result = emit_rewrite(POST_TOOL_CALL, ctx, result)      # 改写先行
|  |  |  +- emit(POST_TOOL_CALL, ctx_with_final_result)             # 观察看到最终值
|  |  |
|  |  +- Exp._make_spawn_fn 闭包内部（非 SpawnTool.execute）:
|  |  |  +- emit(SUBAGENT_START, SubagentContext(agent_id=child_spawn_id, ...))
|  |  |  +- ... 子 agent 执行 ...
|  |  |  +- emit(SUBAGENT_STOP, ctx)
|  |  |
|  |  +- ContextCompactor.compact_if_needed() 完成后:
|  |     +- emit(CONTEXT_COMPACTION, ctx)
|
|  +- finally 块:
|     +- emit(RUN_END, RunContext(task_id, session_id, reason=终止原因))
|
+- Exp finally 块:
   +- _run_cleanup_callbacks()
```

执行顺序语义：
- **PRE_TOOL_CALL**: 先观察再拦截。位于 catalog lookup 之后、structural/input/guard/policy validation 之前，确保所有被尝试调用的工具（含后续被 guard 拒绝的）都能被 hook 观察和拦截。观察 handler 始终执行，不受拦截结果影响。
- **POST_TOOL_CALL / USER_PROMPT_SUBMIT**: 先改写再观察。观察 handler 看到的是改写后的最终数据。
- **SUBAGENT_START/STOP**: 在 `_make_spawn_fn` 闭包内部触发，而非 SpawnTool.execute 中。闭包内可直接访问 `child_spawn_id` 和 HookExecutor。
- **USER_PROMPT_SUBMIT**: 改写是瞬时的，只影响当前执行，不更新持久化历史。下次回放仍使用原始 prompt。这是有意的设计——持久化改写需要改动 chat_history 层，当前无此场景需求。

### 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `matmaster/core/hooks.py` | **重写**：删除旧 Hook Protocol / BaseHook / run_* helpers，放入 HookEvent、context dataclasses、HookOutcome、HookResult、handler 类型别名、HookExecutor |
| `matmaster/types/runtime.py` | `AgentRuntimeSpec.hooks: list[Hook]` -> `hook_executor: HookExecutor \| None` |
| `matmaster/core/exp.py` | `build_runtime()` 中创建 HookExecutor 实例（在 _make_spawn_fn 之前）并注入 spec；`_make_spawn_fn` 闭包内加 SUBAGENT_START/STOP；注入 task_id/session_id 到 spec.meta |
| `matmaster/core/tool_runner.py` | FullToolRunner Phase 1 catalog lookup 后、validation 前加 PRE_TOOL_CALL hook 调用，Phase 2 后加 POST_TOOL_CALL hook 调用。废除 D-01 约束（docstring 中 "Does not call pre_hook/post_hook" 需删除） |
| `matmaster/core/agent.py` | `run_stream` 中加 RUN_START/RUN_END（kernel 是所有路径的公共入口）；`_run_items` 中：构造 UserMessage 前加 USER_PROMPT_SUBMIT hook 调用；压缩完成后加 CONTEXT_COMPACTION hook 调用；删除旧 `run_pre_llm_call` / `run_should_continue` 调用 |
| `matmaster/devshell/stream_hook.py` | 删除旧 Hook Protocol 方法（pre_tool_call 等），保留 on_event() 直接回调。删除 BaseHook 继承 |
| `matmaster/devshell/cli.py` | 删除向 `spec.hooks` list 添加 DevStreamHook 的代码 |
| `matmaster/devshell/runner.py` | 同上 |
| `matmaster/core/__init__.py` | 删除 `BaseHook`, `Hook`, `HookAction` 的 re-export，新增 `HookExecutor`, `HookEvent` 等 |

### DevStreamHook 迁移

DevStreamHook 有两个职责，迁移策略不同：

1. **流式输出观察**（`on_event()` 处理 ThoughtEvent/ResponseEvent/ToolCallEvent/ToolResultEvent）：这是 DevStreamHook 的主要数据通路，由 `devshell/runner.py` 直接喂事件。**保留现有直接回调方式**，不纳入 HookExecutor。两者是互补的——`on_event()` 负责流式渲染，HookExecutor 负责生命周期控制。

2. **旧 Hook Protocol 方法**（`pre_tool_call`、`post_tool_call`、`on_guard_blocked` 等继承自 BaseHook 的方法）：当前未被 FullToolRunner 调用，已是死代码。**直接删除**，不迁移到 HookExecutor。如果 devshell 未来需要工具级 hook，通过 `HookExecutor.on(PRE_TOOL_CALL, ...)` 注册。

## 删除清单

| 删除内容 | 位置 |
|---------|------|
| `Hook` Protocol | `matmaster/core/hooks.py` |
| `HookAction` Enum | `matmaster/core/hooks.py` |
| `BaseHook` 类 | `matmaster/core/hooks.py` |
| `run_pre_tool_call()` 等 6 个 helper 函数 | `matmaster/core/hooks.py` |
| `AgentRuntimeSpec.hooks: list[Hook]` 字段 | `matmaster/types/runtime.py` |
| `InlineToolRunner` 中的 hook 调用 | `matmaster/core/tool_runner.py`（如果 InlineToolRunner 仍存在） |
| `matmaster/hooks/__init__.py` | 更新 docstring 指向新系统，保留空包（`import matmaster.hooks` 在 test_upstream_scenarios 中被测试） |

## 不在范围内

- 配置文件驱动的 hook 注册（当前只需代码注册）
- hook 超时机制（内部 callback 自行负责）
- matcher 模式匹配（Claude Code 的多来源匹配，当前不需要）
- 流式 chunk 观察（保持现有 DevStreamHook 直接回调）
- LLM 调用前后观察（不在用户需求中）
- should_continue 循环控制（不在用户需求中）
- handler 动态去注册（当前仅需 build_runtime 阶段一次性注册）
- GUARD_BLOCKED 独立事件（PRE_TOOL_CALL 位于 validation 之前，guard deny 的工具已可被 hook 观察；guard 拒绝原因可从后续 ToolResult(status="blocked") 中获取）
- USER_PROMPT_SUBMIT 改写的持久化（当前改写为瞬时的，不更新 chat_history 持久化记录）
