# Phase 1: Foundation Contracts - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

定义三层边界契约（PlaygroundContext, AgentRuntimeSpec）、事件类型（AgentEvent/SystemEvent discriminated union）、Guard Protocol 和 MessageBus 事件系统，建立后续所有组件的类型基础。所有新代码放在 matmaster/ 目录下，与 evomaster 完全脱钩。

</domain>

<decisions>
## Implementation Decisions

### 契约模块组织
- 所有新代码放 `matmaster/` 目录下，与 evomaster 脱钩
- `matmaster/contracts/` 独立契约包，包含 context.py、runtime.py、events.py、guards.py
- `matmaster/bus/` 独立事件总线包，包含 queue.py、bridge.py
- 新契约完全独立于 evomaster/utils/types.py 中的现有类型，干净新起点
- 后续 Phase 2-4 的代码（kernel、assembly、playground 重构）也全部放 matmaster/ 下

### AgentEvent 事件设计
- 覆盖当前项目所有已有事件类型，不额外新增
- 分层 union 设计：
  - AgentEvent union：kernel 发射的事件（thought、tool_call、tool_result、finish、error、assistant_state、skill_hit）
  - SystemEvent union：服务层发射的事件（confirmation_request、confirmation_timeout、context_compaction、exp_run、cancelled、workspace_upload_error、bohrium_node、mcp_server_status、mcp_connect）
  - BusEvent = AgentEvent | SystemEvent
- type 字段作为 Pydantic discriminated union 判别字段（Literal type，如 type='thought'）
- ThoughtEvent 单一类型，通过 stream_state 字段区分流式（start/streaming/end）和非流式

### Guard 接口设计
- Guard Protocol 定义 evaluate(ctx: GuardContext) -> GuardResult 接口
- GuardContext 包含：tool_name、tool_args、tool_call_id、current_turn、max_turns、recent_calls
- GuardResult 包含：allowed: bool、reason: str | None、guidance: str | None
- Guard 允许有状态（如 deque 记录近期调用、auth failure count），Protocol 只规定接口不限制内部实现
- TerminationPolicy 不作为独立类型，max_turns 直接作为 AgentRuntimeSpec 的字段
- 重构后去掉 finish tool，终止条件为：LLM 返回无 tool_calls（自然结束）或 max_turns 到达（强制终止）

### MessageBus 消费模式
- 使用同步 queue.Queue，适配 agent 的 ThreadPoolExecutor 同步线程模型（EBUS-01）
- 单消费者模式，QueueBridge 独占消费 MessageBus
- QueueBridge 从 MessageBus 读取 BusEvent，转换为现有 SSE payload 格式（source, type, content, extra），推入现有 SSE queue
- 现有 service 层不需要改动，Phase 5 再统一

### Claude's Discretion
- 各 Event 类型的具体字段设计（在覆盖现有事件语义的前提下）
- GuardContext 中 recent_calls 的具体记录结构
- MessageBus 和 QueueBridge 的内部实现细节
- matmaster/ 的 __init__.py 导出策略

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` -- 项目愿景、核心价值、方案选型、技术栈
- `.planning/REQUIREMENTS.md` -- Phase 1 需求：CONT-01 ~ CONT-05, EBUS-01, EBUS-02
- `.planning/ROADMAP.md` -- Phase 1 目标、依赖、成功标准

### 代码库分析
- `.planning/codebase/ARCHITECTURE.md` -- 现有架构、数据流、事件系统
- `.planning/codebase/CONVENTIONS.md` -- 命名规范、代码风格、模块设计
- `.planning/codebase/STACK.md` -- 技术栈、依赖、运行时

### 现有实现（需了解以设计新契约）
- `evomaster/utils/types.py` -- 现有消息/工具类型体系（Message, ToolSpec, Dialog 等），新契约不引用但需了解语义
- `evomaster/agent/agent.py` -- 现有 BaseAgent/AgentConfig，理解 agent 接口
- `playground/mat_master/core/tool_guard.py` -- 现有 6 关注点 guard 实现，理解 guard 语义
- `playground/mat_master/service/stream_agent.py` -- 现有事件发射模式（event_callback 签名和所有事件类型）
- `playground/mat_master/service/confirm.py` -- 现有 confirmation 事件契约

### 参考架构
- `/Users/kealdoom/Desktop/github/nanobot/nanobot/bus/queue.py` -- nanobot MessageBus 参考实现
- `/Users/kealdoom/Desktop/github/nanobot/nanobot/bus/events.py` -- nanobot 事件类型参考

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `evomaster/utils/types.py` 中的 Pydantic 模式可作为新契约字段设计参考（不复用类型本身）
- `playground/mat_master/service/confirm.py` 的 ConfirmationManager 事件契约可直接映射到 SystemEvent

### Established Patterns
- Pydantic BaseModel 用于所有数据结构（types.py, agent.py）
- `@dataclass` 用于内部状态结构（tool_guard.py）
- Protocol 用于接口定义（现有 ReplyQueueLike）
- 事件通过 callback 函数发射：`event_callback(source: str, event_type: str, content: Any, **extra: Any)`

### Integration Points
- matmaster/contracts/ 被 Phase 2 kernel、Phase 3 assembly、Phase 4 playground 引用
- matmaster/bus/ 被 Phase 2 kernel（发射端）和 QueueBridge（消费端）使用
- QueueBridge 输出接入现有 src/services/stream_service.py 的 SSE queue

</code_context>

<specifics>
## Specific Ideas

- 重构后去掉 finish tool，LLM 返回 stop/无 tool_calls 即为自然结束
- matmaster/ 作为新框架的完整命名空间，后续所有 phase 的新代码都放这里
- TerminationPolicy (CONT-05) 简化为 AgentRuntimeSpec.max_turns 字段，不单独建类型

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 01-foundation-contracts*
*Context gathered: 2026-03-21*
