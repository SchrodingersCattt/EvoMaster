# Project Research Summary

**Project:** matmaster-evo v2.2 AgentKernel Generator-First 全链路改造
**Domain:** Python AsyncGenerator 驱动的 Agent 执行内核重构（brownfield，三阶段迁移）
**Researched:** 2026-04-02
**Confidence:** HIGH

## Executive Summary

本次改造的本质是将 AgentKernel 的事件产生方式从"Hook 副作用推送"转变为"AsyncGenerator 主动 yield"，使事件流成为执行的主通路而非 Hook 的副作用。研究确认该方向与业界三大框架（OpenAI Agents SDK、PydanticAI、LangGraph）的架构演进路径高度一致：所有框架均采用单一内部执行路径（_run_items）同时服务批量调用（run）和流式调用（run_stream），工具执行通过协议接口从 Kernel 解耦。整个改造不需要任何新外部依赖——Python 3.13 标准库（collections.abc.AsyncGenerator、contextlib.aclosing）已覆盖全部需求。

推荐采用三阶段渐进迁移策略。Phase 1 仅改动 Kernel 层（agent.py、新增 tool_runner.py），不影响任何现有调用方——AgentRunService、Exp、DevRunner 全部零修改，1,294 个现有测试无需改动即可通过。Phase 2 将消费路径从 Hook->Bus 迁移到 Generator 直接消费，逐步退役 EventEmitterHook 等辅助 Hook。Phase 3 在所有 Hook 退役后评估是否移除 MessageBus 和 EventRouter 整个中间层。

主要风险集中在两个临界点：Phase 1 的 ContextCompactor 消息列表分裂问题（compactor 原地修改 messages 列表，而 generator 架构将引入分离的消息积累逻辑，导致 KernelRunResult.messages 内容错误），以及 Phase 1-2 边界的双路径重复事件问题（在 EventEmitterHook 完全退役前若 service 层提前消费 run_stream，同一事件将通过两条路径各发送一次）。这两个风险均有明确的代码级预防方案，整体置信度高。

## Key Findings

### Recommended Stack

本次改造零新外部依赖。所有所需能力由 Python 3.13 标准库和现有项目依赖覆盖。关键设计决策：公开接口（run_stream）返回 `AsyncIterator[BusEvent]` 而非 `AsyncGenerator`——消费者只需 `async for`，这是更窄的接口承诺，符合 Liskov 替换原则；私有内部 generator（_run_items）精确标注 `AsyncGenerator[_KernelItem, None]`。内核私有类型（_KernelItem、_KernelState、_TerminalItem）使用 stdlib `@dataclass` 而非 Pydantic——无需验证/序列化开销，实例化快 5-15 倍，与现有 KernelResult 用 `dataclass(frozen=True)` 的项目惯例一致。跨层契约（AgentRuntimeSpec、BusEvent）继续使用 Pydantic BaseModel(frozen=True)。

**核心技术（均已在项目中，无新安装）：**
- Python 3.13 + collections.abc.AsyncGenerator：原生支持泛型下标 `AsyncGenerator[Y, S]`，无需 `typing` 模块
- contextlib.aclosing (stdlib 3.10+)：Phase 2 消费者安全关闭 generator，PEP 525 推荐模式
- dataclasses.dataclass：内核私有类型，比 Pydantic 快 5-15x，_KernelState 需要可变性
- asyncio.gather：InlineToolRunner 并行工具执行，MessageBus 模式已验证
- Pydantic v2（现有）：AgentRuntimeSpec 等跨层契约，arbitrary_types_allowed=True 已启用
- typing.Protocol + runtime_checkable（现有）：ToolRunner Protocol，与现有 Hook/LLMProvider/Guard/EventHandler 一致

**明确不引入：** aiostream/aioreactive（响应式范式不匹配）、pyventus/blinker（正在移除间接 Bus）、anyio/trio（项目绑定 asyncio）、attrs/msgspec（dataclass 已足够）、more-itertools/aioitertools（标准 async for + asyncio.gather 覆盖全部需求）。

### Expected Features

**Phase 1 必须实现（table stakes）：**
- 私有内部 generator _run_items：单一执行路径，run() 和 run_stream() 共同基础，所有终止条件通过 yield terminal + return 表达
- run_stream() 公开 generator 接口：`async def run_stream(...) -> AsyncIterator[BusEvent]`，过滤 _run_items() 只暴露 BusEvent
- run() 向后兼容包装：现有所有调用方零修改，1,294 个测试不需要改动
- 类型化内部流 items（_KernelItem）：三字段 dataclass（event / messages_delta / terminal），每次 yield 可同时携带多类信息
- _KernelState：per-call 局部状态，非 self 属性，保证 Kernel 无状态并发安全
- ToolRunner Protocol 提取：`execute_batch(tool_calls, ctx, *, on_result)` 接口，Kernel 只关心结果列表
- InlineToolRunner 过渡实现：精确包装 agent.py L217-311 三阶段逻辑（guard + pre_hook gating → parallel gather → post_hook），行为等价
- 事件复用（不新增事件类型）：复用现有 18 种 BusEvent，无迁移代价，生产者/消费者接口不变
- 双路径共存（Phase 1）：generator yield 路径与 Hook->Bus 路径并存，无任何外部可见变化
- AgentRuntimeSpec 扩展 5 个 Any|None 预留字段：为 Tool Runtime v2 的 ToolCatalog / RuntimeTopology 等占位

**Phase 2 应实现（differentiators，按价值排序）：**
- AgentRunService.run_agent_stream()：service 层首个真实 run_stream() 消费者，直接对接 SSEHandler + PersistenceHandler，消除 asyncio.Queue 轮询延迟
- Exp.run_stream() passthrough：Exp 层流式接口，包含 cleanup 生命周期保障
- _stream_llm_items() 子 generator：将 _do_stream_llm() 转为逐块 yield，实现与 EventEmitterHook 等价的流式粒度（最复杂单一功能）
- EventEmitterHook 退役：消除整个 Hook->Bus 间接路径（前提：_stream_llm_items() 完成）
- 简单 Hook 退役（AssistantStateHook、SkillHitHook、ContextCompactor bus 依赖）：模式简单，独立无依赖
- on_result callback 即时 yield：工具完成即时推送 ToolResultEvent，用户看到逐个结果出现而非批次结束后全部

**推迟到 Phase 3（条件性）：**
- MessageBus 和 EventRouter 移除：仅在所有 Hook 退役后评估，保留简化版也可接受
- async fanout 替代 Bus+Router：asyncio.Queue pump 模式，零新依赖

**明确不做（anti-features）：**
- 不为流式创建新事件类型，复用现有 BusEvent 类型层级
- 不用 aclose() 实现取消，保持 threading.Event stop_event 机制（aclose() 无法中断 asyncio.to_thread()）
- Phase 1 不移除任何 Hook（无消费者时移除 = 事件消失）
- Phase 1 不移除 Bus/Router
- 不暴露 _KernelItem 为公开 API

### Architecture Approach

当前架构是"observer-push 模型"：Kernel._run_loop() 通过 Hook -> EventEmitterHook -> MessageBus -> EventRouter 将事件推送给消费者，两条并行轨道（messages 列表 + Bus 事件）通过 EventEmitterHook 桥接，事件产出路径隐藏在 Hook 内部，不可组合，上层无法直接从 Kernel 获取结构化事件流。目标架构是"generator-pull 模型"：_run_items() 直接 yield 事件，run() 和 run_stream() 作为独立消费者包装同一 generator，工具执行链通过 ToolRunner Protocol 完全从 Kernel 解耦。三阶段改造保证每阶段独立可验证、不破坏现有功能。

**主要组件（Phase 1 完成态）：**
1. AgentKernel._run_items()：AsyncGenerator，单一执行路径，yield _KernelItem（event / messages_delta / terminal）
2. AgentKernel.run_stream()：公开流式接口，过滤 _run_items() 只 yield BusEvent，返回 AsyncIterator[BusEvent]
3. AgentKernel.run()：向后兼容批量接口，消费 _run_items() 积累 messages_delta + terminal 构造 KernelRunResult
4. ToolRunner Protocol + InlineToolRunner：工具执行链 Protocol 接口，Phase 1 用 InlineToolRunner 精确封装现有逻辑
5. ToolExecutionContext：per-batch 冻结 dataclass，显式传递 turn/max_turns/stop_event，ToolRunner 无状态可重入
6. AgentRuntimeSpec（扩展）：5 个 Any|None 预留字段，spec.tool_runner != None 时 Kernel 使用注入 runner，否则回退 InlineToolRunner
7. _KernelState：per-call 局部变量（非 self），messages / turn / total_usage / last_stop_reason 及 Tool Runtime v2 预留字段

**关键模式：**
- Pattern 1（Generator Consumption Isolation）：run() 和 run_stream() 各自独立消费 _run_items()，每次调用创建新 generator 实例
- Pattern 2（ToolRunner Fallback Chain）：Kernel 内部按 spec.tool_runner 是否为 None 决定用注入 runner 还是 InlineToolRunner，Phase 2 只需 Exp 注入，Kernel 零改动
- Pattern 3（Tool Definitions Resolution Abstraction）：_resolve_tool_definitions() 封装 tool_catalog/tool_registry 回退逻辑，版本缓存为 Phase 2 ToolCatalog 预备
- Pattern 5（on_result Callback Pending Items）：ToolRunner 的 on_result 回调通过 pending_items 列表传递，execute_batch 返回后统一 yield，绕过"callback 内不能 yield"的限制

### Critical Pitfalls

1. **ContextCompactor 消息列表分裂（Phase 1，CRITICAL）** — compactor.compact_if_needed() 原地修改 messages 列表（context_compactor.py L228/L236），generator 架构若使用增量 messages_delta 积累，run() 积累的副本在压缩后与 KernelState.messages 发散，KernelRunResult.messages 将包含过时内容，破坏对话历史。解决方案：_TerminalItem 携带最终 messages 快照，run() 从 terminal.messages 获取最终列表，而非纯依赖 messages_delta 积累。

2. **Phase 1-2 边界双路径重复事件（Phase 2 边界，CRITICAL）** — 在 EventEmitterHook 完全退役前若 service 层提前消费 run_stream()，同一 ToolResultEvent 通过 generator yield 路径和 Hook->Bus 路径各送达一次，导致数据库重复记录和前端重复推送，且两路径事件顺序不同（generator 按 tool_calls 顺序，Hook 按 asyncio.gather 完成顺序）。解决方案：Phase 2 严格有序——先退役 EventEmitterHook，再切换 service 层到 run_stream()。

3. **aclose() 无法取消进行中的工具执行（Phase 1，HIGH）** — consumer 提前退出触发 aclose()，但 GeneratorExit 无法中断 asyncio.to_thread() 包装的同步子进程（BashTool 等），导致工具后台继续执行、资源泄漏。解决方案：run_stream() API 合约明确声明取消机制是 threading.Event stop_event，不能依赖 break/aclose()；consumer 退出前必须 set stop_event。

4. **InlineToolRunner 取消粒度退化（Phase 1，HIGH）** — 当前 agent.py L222-229 在 guard 评估循环内逐个检查 stop_event，批次中取消立即生效。提取为 execute_batch() 后若不检查 stop_event，取消粒度从单个 tool_call 级退化为整批次前。解决方案：在 InlineToolRunner guard 循环中保留 stop_event 检查，开销可忽略。

5. **yield 在 try/finally 中的禁止（Phase 1，HIGH）** — _run_items() 若在 try/finally 的 finally 块中 yield（如 yield terminal on error），将抛出 RuntimeError: async generator ignored GeneratorExit（PEP 525 明确禁止）。解决方案：terminal item 作为正常流最后一个 yield，不在 finally 中 yield；异常场景接受不产出 terminal event，由上层 run()/run_stream() 处理异常。

## Implications for Roadmap

基于研究，建议三阶段路线图，每阶段独立可验证，严格有序：

### Phase 1: Kernel Generator Foundation（Kernel 层基础重构）

**Rationale:** 纯 Kernel 层改动，所有现有调用方（AgentRunService、Exp、DevRunner）零修改，1,294 个现有测试无需改动即可通过。Phase 1 完成后 generator 路径存在但没有任何真实消费者，双路径共存无任何外部可见影响——这是最低风险的起点。

**Delivers:**
- _run_items() generator 替代 _run_loop()，成为单一执行路径
- run() 向后兼容包装，行为语义等价，全部现有测试通过
- run_stream() 新公开接口，供 Phase 2 消费者接入
- ToolRunner Protocol + InlineToolRunner，工具执行从 Kernel 完全解耦
- AgentRuntimeSpec 5 个预留字段，为 Tool Runtime v2 铺路

**Addresses:** FEATURES.md 中全部 table stakes（Phase 1 部分）

**Avoids:**
- Pitfall 1（ContextCompactor 列表分裂）：_TerminalItem 携带 messages 快照
- Pitfall 14（yield 在 finally 中）：terminal 作为最后正常 yield
- Pitfall 4（stop_event 粒度退化）：InlineToolRunner guard 循环保留检查
- Pitfall 5（Kernel 实例属性并发污染）：_KernelState 纯局部变量，enforce `assert not vars(kernel)`

**Build order（内部并行优化）：**
Chunk 1（ToolRunner Protocol + InlineToolRunner）并行 Chunk 2（AgentRuntimeSpec 扩展）→ Chunk 3（_KernelItem/_KernelState/_TerminalItem 类型）→ Chunk 4（_run_items() generator 重写，最大单一变更）→ Chunk 5（run_stream()）+ Chunk 6（run() 委托，可并行）→ 全量回归测试

### Phase 2: Exp + Service Layer Migration + Hook Retirement（上层迁移与 Hook 退役）

**Rationale:** Phase 1 建立 generator 基础后，Phase 2 将真实消费者接入 generator 路径并逐步退役 Hook->Bus 路径。关键约束：EventEmitterHook 退役必须先于 service 层切换 run_stream() 消费——违反此顺序将触发 Pitfall 3（重复事件）。

**Delivers:**
- _stream_llm_items() 子 generator：真正流式 LLM 事件（逐块 yield），替代 EventEmitterHook 的 on_stream_chunk/on_segment_complete
- Exp.run_stream()：Exp 层流式接口，context manager 包装确保 cleanup 必然执行
- AgentRunService.run_agent_stream()：service 层首个真实消费者，直接对接 SSEHandler + PersistenceHandler（消除 asyncio.Queue 100ms 轮询延迟）
- EventEmitterHook 完全退役
- 简单 Hook 退役：AssistantStateHook、SkillHitHook、ContextCompactor bus 依赖

**Uses:** contextlib.aclosing（STACK.md）确保 Exp.run_stream() consumer 异常退出时 generator 正确清理

**Implements:** 服务层直接 fanout 模式（`async for event: SSEHandler.handle(); PersistenceHandler.handle()`）

**Avoids:**
- Pitfall 3（重复事件）：严格顺序—退役 EventEmitterHook → 再切换 service 消费者
- Pitfall 6（OutputProcessorHook 业务元数据丢失）：auto_save/summarize 迁移到 ToolResult.info 字段
- Pitfall 10（_do_stream_llm() hook 调用遗漏）：转换前建立完整事件捕获测试基线，逐一核对
- Pitfall 15（Exp cleanup 保障）：Exp.run_stream() 用 asynccontextmanager / try/finally 包装

### Phase 3: Bus Removal Assessment（MessageBus 退役评估，条件性）

**Rationale:** Phase 2 完成后评估剩余 Bus 消费者。若仅剩 service 层直接 emit（ErrorEvent、CancelledEvent、McpConnectEvent、BohriumNodeEvent 等 Kernel 外事件），则 Bus 可被简单 async fanout 替代。若迁移成本高于收益，保留简化版 Bus 也是合理的。Phase 3 是条件性的——即使不做，Phase 2 已实现核心价值。

**Delivers:**（如果执行）删除 bus.py 和 event_router.py；async fanout 函数替代 Bus+Router；代码复杂度和内存最终最小化

**Avoids:**
- Pitfall 11（ContextCompactor Bus 依赖遗漏）：审计全部 `self._bus.emit()` 调用，不只 Hook 来源
- 静默事件丢失：Bus 移除前建立事件数量基准对比测试，移除后计数必须一致

### Phase Ordering Rationale

- Phase 1 优先：纯 Kernel 内部改动，外部调用方零感知，独立可验证。Phase 2 的 run_stream() 消费者需要 Phase 1 交付的接口才能构建。
- Phase 2 在 Phase 1 后：run_stream() 公开接口（Phase 1 交付）是 Phase 2 Exp.run_stream() 和 AgentRunService.run_agent_stream() 的前提。EventEmitterHook 不能在 run_stream() 有消费者前退役。
- Phase 3 在 Phase 2 后且条件性：需要所有 Hook->Bus 路径已退役，否则删除 Bus = 事件丢失。
- Phase 1 内部 Chunk 1+2 可并行：ToolRunner Protocol 和 AgentRuntimeSpec 扩展互不依赖。
- Chunk 4（_run_items()）是最大单一变更，等待 Chunk 1+2+3 就绪后开始，完成后触发全量测试。

### Research Flags

需要 deeper research 的 Phase：
- **Phase 2 的 _stream_llm_items() 转换：** _do_stream_llm() 是 130+ 行代码（agent.py L482-643），包含 producing_reasoning/producing_content 状态机和嵌套 try/finally，每个 hook 调用点需精确映射到 yield。转换前需建立完整事件类型捕获测试基线。
- **Phase 2 的 ConfirmationHook 迁移：** 涉及双向流（emit request -> wait for reply），是最复杂的 Hook，需要单独的迁移方案研究，Phase 2 应最后处理。
- **Phase 3 的 Bus 消费者审计：** 需 grep 全部 `bus.emit()` 调用（含 BohriumSetupService、WorkspaceHandler 等 Kernel 外组件），评估迁移复杂度后再决策是否执行 Phase 3。

标准模式（可跳过 research-phase）：
- **Phase 1 的 ToolRunner Protocol + InlineToolRunner：** 与现有 4 个 Protocol 完全一致的模式，项目团队已熟悉，直接实现。
- **Phase 1 的 AgentRuntimeSpec 扩展：** 简单字段添加，arbitrary_types_allowed=True 已启用，直接实现。
- **Phase 2 的简单 Hook 退役（AssistantStateHook、SkillHitHook）：** 模式清晰（yield event 替代 hook callback），独立无依赖，直接实现。

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | 全部为 stdlib + 现有依赖，Python 3.13.2 本地验证通过，无新依赖风险。 |
| Features | HIGH | 基于 OpenAI Agents SDK、PydanticAI、LangGraph 三大框架设计文档和 matmaster spec（1500+ 行）交叉验证，table stakes 和 anti-features 论证充分。 |
| Architecture | HIGH | 基于完整 codebase 源码分析（agent.py/exp.py/bus.py/hooks.py 等全部受影响文件），三阶段演进路径清晰，每阶段改动文件明确列出，build order 已验证依赖关系。 |
| Pitfalls | HIGH | 所有 Critical/HIGH 级别 pitfall 均有代码行级定位（context_compactor.py L228/L236，agent.py L222-229 等），PEP 525/789 + CPython issue tracker 支撑，预防方案明确。 |

**Overall confidence:** HIGH

### Gaps to Address

- **Phase 3 条件判断标准：** 研究确定了"审计 Bus 消费者"的方向，但具体的去 Bus 决策标准（非 Kernel 消费者的迁移成本阈值）需要 Phase 2 完成后通过实际 grep 审计决定。处理方式：Phase 3 规划时先执行全量 Bus 消费者审计，再决策。

- **ConfirmationHook 双向流迁移路径：** 标记为最复杂的 Hook，需要单独研究双向流（emit request -> wait for reply）在 generator 架构下的实现模式。处理方式：Phase 2 规划时作为独立子课题单独 research，排在 Hook 退役序列最后。

- **Phase 3 多消费者 fanout 的背压和排序：** async fanout 的概念验证充分（asyncio.Queue pump 模式），但多消费者场景下的背压处理（SSE 慢消费 vs 持久化快消费）和事件顺序保证需要 Phase 3 具体实现时验证。处理方式：Phase 3 启动时先建立 fanout 性能基准测试。

- **DevRunner 迁移时机：** DevRunner 当前通过 Hook 路径获得事件，Phase 2 后可选择迁移到 run_stream()，但具体时机和必要性未有定论。处理方式：Phase 2 完成后评估 DevStreamHook 是否仍满足开发调试需求，按需决定，不阻塞主路。

## Sources

### Primary (HIGH confidence)
- [Python 3.13 collections.abc 官方文档](https://docs.python.org/3.13/library/collections.abc.html) — AsyncGenerator vs AsyncIterator 类型层级
- [PEP 525 -- Asynchronous Generators](https://peps.python.org/pep-0525/) — aclose() 语义、GeneratorExit 约束、yield-in-finally 禁止
- [PEP 789 -- Preventing task-cancellation bugs](https://peps.python.org/pep-0789/) — async generator cancel scope 问题
- [cpython issue #112866](https://github.com/python/cpython/issues/112866) — AsyncIterator vs AsyncGenerator 类型注解指导
- [OpenAI Agents SDK Streaming](https://openai.github.io/openai-agents-python/streaming/) — run_streamed() 架构、StreamEvent 类型
- [PydanticAI Agent API](https://ai.pydantic.dev/api/agent/) — run() / run_stream() / run_stream_events() 接口设计对比
- [LangGraph Streaming Docs](https://docs.langchain.com/oss/python/langgraph/streaming) — astream()、StreamManager fanout 模式
- matmaster design spec: docs/specs/2026-04-02-kernel-generator-first.md（1500+ 行，含 spec D-07 等关键设计决策）
- matmaster Tool Runtime v2 spec: docs/specs/2026-04-02-tool-runtime-v2.md
- 完整 codebase 源码分析：agent.py（700 行）、exp.py（517 行）、context_compactor.py（367 行）、hooks.py（287 行）、bus.py（64 行）及全部 5 个 service-layer hooks

### Secondary (MEDIUM confidence)
- [cpython issue #112572](https://github.com/python/cpython/issues/112572) — asyncio.gather + generator + return_exceptions 交互
- [cpython issue #79590](https://github.com/python/cpython/issues/79590) — async generator aclose() 边界情况
- [AdalFlow: Agent Streaming Architecture Analysis](https://adalflow.sylph.ai/design/agent-streaming.html) — OpenAI SDK 内部事件流分析
- [PydanticAI Agent Lifecycle (DeepWiki)](https://deepwiki.com/pydantic/pydantic-ai/2.1-agent-run-lifecycle) — graph node 类型、AgentRun 执行模型

### Tertiary (LOW confidence)
- Phase 3 多消费者 async fanout 的背压和排序保证：概念验证充分，但多消费者场景的具体行为（背压策略、有序性保证）需要 Phase 3 实际实现后验证。

---
*Research completed: 2026-04-02*
*Ready for roadmap: yes*
