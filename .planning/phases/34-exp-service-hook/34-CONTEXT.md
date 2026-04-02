# Phase 34: Exp/Service 接入 + Hook 退役 - Context

**Gathered:** 2026-04-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Generator 事件流贯穿 Kernel→Exp→Service 全链路。FullToolRunner 激活为默认执行路径（无灰度开关）。4 个 Hook（EventEmitterHook / AssistantStateHook / SkillHitHook / OutputProcessorHook）全部退役。ContextCompactor 的 Bus 依赖解除。Hook→Bus 间接事件路径移除。现有 run()/run_agent() 行为不变。

</domain>

<decisions>
## Implementation Decisions

### Plan 拆分策略
- **D-01:** Phase 34 分为 3 个 plan，对应 advancement plan 的 Wave B1（拆为 2）+ Wave B2（保持 1）：
  - Plan 1 — 内核改造：_stream_llm_items() 子 generator + Exp.run_stream() + skill overlay 修正（改走 ToolCatalog.register_overlay()）+ Exp.build_runtime() 注入 FullToolRunner + ToolCatalog + RuntimeTopology
  - Plan 2 — Service 层切流：AgentRunService.run_agent_stream() + ToolResult.payload/meta 到 SSE/持久化前端契约的兼容映射 + source 归一化（MatMaster / MatMaster:<exp>）
  - Plan 3 — Hook 退役：EventEmitterHook → AssistantStateHook → SkillHitHook → OutputProcessorHook → ContextCompactor bus 依赖 → Hook→Bus 路径移除

### FullToolRunner 启用策略
- **D-02:** Wave B1（Plan 1）直接让 FullToolRunner 成为默认执行路径。不使用灰度开关（meta["tool_runtime_v2_enabled"]）。原因：KGEN-06 已在 Phase 33 提前完成，_run_items() 已 yield ToolCallEvent/ToolResultEvent，不再依赖 Hook 路径产出 tool 事件。减少中间态。

### ESIN-08 处置
- **D-03:** 丢弃 ESIN-08（system prompt 工具枚举段处理）。不再考虑此需求。从 REQUIREMENTS.md 中移除。

### _stream_llm_items() 改造
- **D-04:** 将 _do_stream_llm() 拆为 _stream_llm_items() sub-generator + 聚合器。sub-generator 逐 chunk yield ThoughtEvent/ResponseEvent，segment complete 时 yield 与 EventEmitterHook.on_segment_complete() 等价的事件。最终仍返回完整 LLMResponse。保留 content_parts/reasoning_parts/tool_calls_acc 聚合语义，stop_event 和 retry/backoff 逻辑不回退。

### run_agent_stream() 事件消费架构
- **D-05:** run_agent_stream() 将 Exp.run_stream() 产出的事件送入现有 bus/router。暂不移除 EventRouter（Phase 36 去总线化负责）。只替换事件来源——从 Hook emit 改为 generator 事件 → bus.emit。复用现有 playground/Bohrium/history/quota/cleanup 框架。

### Hook 退役顺序
- **D-06:** 逐个渐进退役，顺序遵循 advancement plan Task 3：
  1. EventEmitterHook — Kernel 事件替代（generator 已直接产出 tool_call/tool_result/thought/response）
  2. AssistantStateHook — _run_items() 直接 yield AssistantStateEvent
  3. SkillHitHook — _run_items() 或 ToolRunner 直接产出 SkillHitEvent
  4. OutputProcessorHook — auto_save/summarize 标记写入 ToolResult.payload 或 ToolResult.meta，不再额外发第二个 ToolResultEvent
  5. ContextCompactor — event_sink + deque 暂存方案，_stream_llm_items() 完成后 yield 暂存的 ContextCompactionEvent
  6. 全部退役后移除 Hook→Bus 间接事件路径

### ContextCompactor 迁移方案
- **D-07:** compactor 不再持有 bus 引用，改为接受 event_sink: Callable[[BusEvent], Awaitable[None]] 回调。_stream_llm_items() 注入 sink，sink 将事件暂存到本地 deque。LLM 调用完成后检查 deque，yield 暂存的 ContextCompactionEvent。

### Skill overlay 路径修正
- **D-08:** on_skill_hit 改为通过 ToolCatalog.register_overlay() 注册 MCP 工具（经 ToolCompiler 编译）。每次 overlay 注入 bump version，驱动 Kernel _resolve_tool_definitions() 刷新 tool_definitions。

### 从 Phase 33 延续的决策
- **D-09:** FullToolRunner 不调用 pre_hook/post_hook（Phase 33 D-01 延续）。Hook 退役后这些 dispatch 函数不再被调用。
- **D-10:** execute_batch 逐工具返回结果，不整批失败（advancement plan D-06 延续）。

### Claude's Discretion
- _stream_llm_items() 内部 yield 点的精确位置（在保持 segment-complete 语义等价的约束下）
- run_agent_stream() 中 bus.emit 与 generator 消费的具体桥接实现
- Hook 退役过程中测试文件的具体组织方式
- ConfirmationHook 不在退役范围内（FUTR-02），Phase 34 保持其在 hooks 列表中继续运行

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 推进设计（核心）
- `docs/plans/2026-04-02-v2.2-phase2-advancement.md` — v2.2 Phase 2 三波次推进设计。Phase 34 对应 Task 2 (Wave B1) 和 Task 3 (Wave B2)。Task 2 步骤 1-9 是 generator 主链打通 + Exp 注入。Task 3 步骤 1-8 是 Hook 逐个退役。包含 6 个关键设计决策（D-01~D-06）和 6 个风险项（R-01~R-06）

### 架构 Spec
- `docs/specs/2026-04-02-kernel-generator-first.md` — Kernel generator-first 设计，_run_items() / run_stream() / _stream_llm_items() 的接口定义和事件语义
- `docs/specs/2026-04-02-tool-runtime-v2.md` — Tool Runtime v2 架构设计，FullToolRunner 七步执行链、ToolCatalog overlay/version 机制、三层约束模型

### Phase 32/33 产出（已实现的基础设施）
- `matmaster/core/agent.py` — AgentKernel，_run_items() generator + run_stream() + _do_stream_llm()（即将改造为 _stream_llm_items()）
- `matmaster/core/tool_runner.py` — FullToolRunner（七步执行链，不调 hook）+ InlineToolRunner（保留作 fallback）
- `matmaster/tools/tool_catalog.py` — ToolCatalog，register_overlay() + version 机制 + ToolCompiler 编译
- `matmaster/types/tool_spec.py` — ToolSpec / ToolBinding / ResourceClaim / ToolInstance
- `matmaster/types/topology.py` — RuntimeTopology / SessionCapabilities / ToolPlane
- `matmaster/core/structural_validation.py` — StructuralValidation（无状态参数校验）
- `matmaster/core/capability_policy.py` — CapabilityPolicy（effect_level + plane/capability）
- `matmaster/core/tool_scheduler.py` — ToolScheduler（exclusive/shared_read/counted）
- `matmaster/tools/tool_compiler.py` — ToolCompiler（注册时一次性编译）

### Phase 34 改动目标文件
- `matmaster/core/exp.py` — Exp.build_runtime()（注入 FullToolRunner/ToolCatalog）+ 新增 run_stream()
- `src/services/agent_run_service.py` — 新增 run_agent_stream() + _build_service_hooks() 调整
- `matmaster/core/hooks.py` — EventEmitterHook 删除（保留 BaseHook/Hook Protocol/dispatch 函数）
- `matmaster/hooks/assistant_state.py` — AssistantStateHook 退役删除
- `matmaster/hooks/skill_hit.py` — SkillHitHook 退役删除
- `matmaster/hooks/output_processor.py` — OutputProcessorHook 退役删除
- `matmaster/core/context_compactor.py` — bus 依赖改为 event_sink
- `matmaster/integration/event_payloads.py` — ToolResult.payload→info 兼容映射
- `matmaster/tools/skill_tool.py` — on_skill_hit 改走 catalog.register_overlay()

### 需求定义
- `.planning/REQUIREMENTS.md` — Phase 34 映射的 15 个 requirement（ESIN-01~07, HRET-01~06, REGR-02）。ESIN-08 已丢弃。KGEN-06 已在 Phase 33 完成。

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/core/agent.py` L236-277: run_stream() 已完整实现，消费 _run_items() 并 yield event
- `matmaster/core/agent.py` L626-787: _do_stream_llm() 流式状态机，是 _stream_llm_items() 的改造基础
- `matmaster/core/agent.py` L306-310: _run_items() 中 InlineToolRunner fallback 逻辑，Phase 34 后此 fallback 不再触发
- `matmaster/core/agent.py` L416-456: _run_items() 已 yield ToolCallEvent/ToolResultEvent（KGEN-06）
- `matmaster/core/hooks.py` L182-286: EventEmitterHook 完整实现（pre_tool_call→ToolCallEvent, post_tool_call→ToolResultEvent, on_stream_chunk→Thought/Response），是退役后 generator 事件等价性的参照基准
- `matmaster/core/exp.py` L149-244: build_runtime() 当前组装逻辑，注入点在 L228-234 的 model_copy
- `matmaster/core/exp.py` L183-185: EventEmitterHook 创建并 append 到 hooks 列表
- `matmaster/core/exp.py` L411-516: _init_skill_tools() 中 on_skill_hit 闭包，当前直接 registry.register()
- `src/services/agent_run_service.py` L132-158: _build_service_hooks() 组装 service 层 hooks
- `src/services/agent_run_service.py` L392-398: Stage 6 kernel.run() 调用点
- `matmaster/integration/event_payloads.py` L107-116: tool_result 事件的 info 字段映射（读 payload.get('payload')）
- `matmaster/tools/tool_catalog.py` L63-74: register_overlay() 已实现 version bump

### Established Patterns
- frozen Pydantic model 层间契约（PlaygroundContext, AgentRuntimeSpec）
- @runtime_checkable Protocol 接口（Tool, Hook, Guard, LLMProvider, Session, ToolRunner）
- TYPE_CHECKING + lazy import 解决循环导入
- event_payloads.py 作为唯一公共 SSE/持久化映射层
- _normalize_public_source() 将内部 source 归一化为 MatMaster / MatMaster:<subtype>

### Integration Points
- Exp.build_runtime() 是 FullToolRunner/ToolCatalog/RuntimeTopology 的注入点
- AgentRuntimeSpec.model_copy(update={...}) 是 service 层 hook 注入和 spec 扩展的标准模式
- EventRouter._consume_loop() 是 bus 事件的唯一消费者，run_agent_stream() 需要将 generator 事件桥接到 bus
- ChatHistoryConverter.events_to_messages() 依赖 source 过滤（MatMaster / MatMaster:*）

</code_context>

<specifics>
## Specific Ideas

- Advancement plan Task 2 的步骤顺序仍是有效参考，但需跳过灰度开关相关步骤（Step 2 中的 meta flag 部分）
- _stream_llm_items() 的改造应建立事件捕获测试基线——先记录现有 EventEmitterHook 产出的事件序列，再验证 generator 等价
- OutputProcessorHook 的 auto_save/summarize 逻辑是按 tool_name 子串匹配（如 "write"、"edit"），迁移时需保持相同匹配规则
- ConfirmationHook 继续在 hooks 列表中运行（FUTR-02），Phase 34 的 Hook 退役不影响它
- run_agent_stream() 需要在 terminal 事件后继续补发 stream_closed（保持现有 SSE 关闭语义）

</specifics>

<deferred>
## Deferred Ideas

- ESIN-08（system prompt 工具枚举段处理）— 用户决定丢弃此需求
- ConfirmationHook 双向流在 generator 模型中的替代方案 — FUTR-02（v2.3+）
- 去总线化（MessageBus + EventRouter 移除）— Phase 36

</deferred>

---

*Phase: 34-exp-service-hook*
*Context gathered: 2026-04-02*
