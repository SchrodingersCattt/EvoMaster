# Phase 2: Agent Kernel - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

实现纯执行循环（AgentKernel）和 LLM Provider 抽象，交付可用 mock spec 独立测试的 kernel。Kernel 只消费 AgentRuntimeSpec，不做 config 装配。包含 GuardPipeline、Hook 扩展点、LLMProvider Protocol 及一个具体实现。

</domain>

<decisions>
## Implementation Decisions

### 循环终止行为
- LLM 返回无 tool_calls → 直接结束，发射 FinishEvent(reason='natural')。不再给"再试一次"的机会
- max_turns 到达 → FinishEvent(reason='max_turns')，用同一事件类型通过 reason 区分
- 支持 threading.Event 外部取消，每轮开始前检查，设置后发射 FinishEvent(reason='cancelled')
- 工具串行执行，一次处理一个 tool call。并行能力留给 exp 层或后续优化，不是 kernel 职责
- 去掉 finish tool（Phase 1 已决定），终止条件只有：自然结束 / max_turns / 外部取消

### Hook 扩展模型
- Hook 是可拦截式扩展点，不是纯观察者
- 单一 Hook Protocol + BaseHook 基类（所有方法返回默认值），实现者只需 override 关心的方法
- 4 个 hook point：pre_tool_call (返回 HookAction: CONTINUE/SKIP)、post_tool_call (观察)、pre_llm_call (观察)、should_continue (返回 bool)
- 多个 hook 按注册顺序串行执行，第一个返回拦截结果（SKIP/False）的立即生效，后续 hook 不执行
- 事件发射通过 EventEmitterHook + MessageBus 组合实现：kernel 不直接持有 MessageBus，由 exp 层注入 EventEmitterHook 完成事件转发
- on_stream_chunk 作为 hook 的一部分，将 streaming token 转发给事件系统

### LLM Provider 职责边界
- LLMProvider Protocol 包含 chat() 和 chat_stream() 两个核心方法
- chat() 返回完整 LLMResponse，chat_stream() 返回 Iterator[StreamChunk]
- retry 策略内置于 provider 实现，kernel 不管重试
- kernel 默认使用 chat_stream()，通过 hook 将 streaming chunks 转发给事件系统（前端实时看到 token 流）
- 新定义 matmaster/ 下的消息类型（Message/LLMResponse/StreamChunk），与 evomaster/utils/types.py 完全脱钩
- Phase 2 实现一个具体的 LLMProvider（如 OpenAIProvider）验证 Protocol 可用性，不只是 mock

### Guard 拦截反馈方式
- 被 guard 拦截的 tool call 通过 ToolMessage 错误响应返回给 LLM，包含 reason 和 guidance
- GuardPipeline 内置 LoopDetectionGuard（滑动窗口检测重复调用），不可移除
- MaxTurns 由循环计数器处理，不需要单独 guard
- 业务 guard（manuscript gate、auth failure gate 等）由 exp 层通过 AgentRuntimeSpec.guards 注入
- Guard 评估时机：每个 tool call 执行前
- 执行顺序：Guard 评估 → pre_tool_call hook → tool 执行 → post_tool_call hook。Guard 拦截的调用不触发 hook

### Claude's Discretion
- LLMResponse / StreamChunk / Message 等新消息类型的具体字段设计
- LoopDetectionGuard 的窗口大小和阈值参数（可参考现有 LOOP_WINDOW=5, LOOP_THRESHOLD=2）
- HookAction 枚举的具体值
- GuardPipeline 内部 recent_calls 滑动窗口的维护方式
- 具体 LLMProvider 实现的选择（OpenAI vs LiteLLM）
- kernel 内部状态管理细节

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` — 项目愿景、核心价值、方案选型、nanobot 参考架构
- `.planning/REQUIREMENTS.md` — Phase 2 需求：KERN-01 ~ KERN-04, LLMP-01
- `.planning/ROADMAP.md` — Phase 2 目标、成功标准、依赖关系

### Phase 1 交付物（Phase 2 直接依赖）
- `matmaster/contracts/runtime.py` — AgentRuntimeSpec 定义（llm_provider/tool_registry/guards/hooks/max_turns）
- `matmaster/contracts/guards.py` — Guard Protocol、GuardContext、GuardResult 定义
- `matmaster/contracts/events.py` — AgentEvent/SystemEvent/BusEvent discriminated union
- `matmaster/contracts/context.py` — PlaygroundContext 定义
- `matmaster/bus/queue.py` — MessageBus 同步事件总线
- `matmaster/bus/bridge.py` — QueueBridge SSE 桥接适配器

### 代码库分析
- `.planning/codebase/ARCHITECTURE.md` — 现有架构、数据流、ThreadPoolExecutor 线程模型
- `.planning/codebase/CONVENTIONS.md` — 命名规范、代码风格、Protocol 使用模式
- `.planning/codebase/STRUCTURE.md` — 目录结构、新代码放置位置

### Phase 1 上下文
- `.planning/phases/01-foundation-contracts/01-CONTEXT.md` — Phase 1 决策（新代码在 matmaster/、去掉 finish tool、同步 queue、Guard Protocol 设计）

### 现有实现（需理解以设计 kernel）
- `evomaster/agent/agent.py` — 现有 BaseAgent.run() 和 _step() 执行循环
- `playground/mat_master/core/agent.py` — 现有 MatMasterAgent._step() 业务扩展（tool guard、parallel exec、job registry）
- `evomaster/utils/llm.py` — 现有 BaseLLM / OpenAILLM / AnthropicLLM 实现（query/query_stream/_call_with_retry）
- `playground/mat_master/core/tool_guard.py` — 现有 6 关注点 guard 实现（loop detection 参数、guard 评估模式）

### 参考架构
- `/Users/kealdoom/Desktop/github/nanobot/nanobot/` — nanobot kernel：AgentLoop + ToolRegistry + LLMProvider 设计参考

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/contracts/guards.py` Guard Protocol — kernel 直接使用，不需要重新定义
- `matmaster/contracts/runtime.py` AgentRuntimeSpec — kernel 的输入契约，需要更新 llm_provider/hooks 的 Any 类型为具体 Protocol
- `matmaster/contracts/events.py` AgentEvent — FinishEvent/ThoughtEvent/ToolCallEvent/ToolResultEvent 已定义，kernel 通过 hook 发射
- `matmaster/bus/queue.py` MessageBus — EventEmitterHook 使用

### Established Patterns
- Pydantic frozen model 用于不可变契约（AgentRuntimeSpec、PlaygroundContext）
- `@runtime_checkable` Protocol 用于接口定义（Guard）— Hook 和 LLMProvider 应沿用
- 同步 threading 模型 — agent 运行在 ThreadPoolExecutor 中，kernel 的所有 IO 都是同步的
- 事件通过 MessageBus emit，不通过 callback 直传 — Phase 1 已确立

### Integration Points
- `matmaster/kernel/` — Phase 2 新代码位置（AgentKernel、GuardPipeline、Hook、LLMProvider）
- AgentRuntimeSpec — kernel 的输入，exp 层的输出。Phase 2 需要更新 spec 中 llm_provider/hooks 字段的类型
- MessageBus — kernel 不直接使用，通过 EventEmitterHook 间接连接
- Phase 3 (Exp Assembly) 将构建 AgentRuntimeSpec 并注入 hooks/guards

</code_context>

<specifics>
## Specific Ideas

- 工具串行执行是有意的简化决策：kernel 保持简单，并行能力由 exp 层或后续优化引入
- EventEmitterHook 是 kernel 与事件系统的唯一桥梁，kernel 本身不感知 MessageBus 的存在
- chat_stream() 作为默认调用方式而非 chat()，因为前端需要实时 token 流（现有系统已是 streaming 模式）
- LLMProvider 需要实现一个具体 provider（不只是 mock），确保 Protocol 在实际 LLM 调用场景下可用

</specifics>

<deferred>
## Deferred Ideas

- 工具并行执行（基于 depend_on 或无依赖分组）— 重构稳定后优化
- Context compaction — kernel 需要支持但具体策略留给后续（CompactionConfig 已在 spec 中）

</deferred>

---

*Phase: 02-agent-kernel*
*Context gathered: 2026-03-22*
