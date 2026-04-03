# Phase 32: Kernel Generator + Tool Runtime v2 核心骨架 - Context

**Gathered:** 2026-04-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Kernel 拥有 generator-first 执行路径（`_run_items()` / `run_stream()` / `run()`），Tool Runtime v2 类型体系（ToolSpec/ToolBinding/ToolInstance/ResourceClaim/ToolDecision/SessionCapabilities/RuntimeTopology/ToolPlane）全部就位，ToolCatalog 以 facade 模式运行，InlineToolRunner 抽出工具执行链，AgentRuntimeSpec 扩展字段注入。所有现有调用方零修改、全量测试通过。

</domain>

<decisions>
## Implementation Decisions

### Plan 拆分策略
- **D-01:** 25 个 requirement 分 3 个 plan 实现：
  - Plan A: 类型体系 + ToolResult 升级（TOBJ-01~08, TRES-01）
  - Plan B: ToolCatalog + ToolRunner + AgentRuntimeSpec 扩展（TCAT-01~03, TRUN-01~02, TRUN-05, SPEC-01, TDEF-01, TCON-02）
  - Plan C: Kernel generator 改造 + 回归验证（KGEN-01~05, REGR-01, REGR-03）

### ToolResult 迁移路径
- **D-02:** ToolResult 一步到位替换：删除 `info` 字段，新增 `payload: dict[str, Any]` + `meta: dict[str, Any]`。影响面仅 3 处代码（`output_processor.py:45`、`hooks.py:227`、`events.py:72` 的 ToolResultEvent.info）+ 相关测试，不值得做兼容层。

### AgentRuntimeSpec 字段类型
- **D-03:** AgentRuntimeSpec 新增的 5 个字段（tool_runner / tool_catalog / runtime_topology / capability_policy / structural_validation）直接使用具体类型注解（如 `ToolRunner | None`），不用 `Any`。Phase 32 同时定义类型体系和扩展 spec，循环导入通过 `TYPE_CHECKING` 解决。

### ToolCatalog facade 边界
- **D-04:** Phase 1 ToolCatalog 采用纯委托 facade 模式（spec 原案）：所有操作委托给内部 ToolRegistry。`get_tool()` 从 registry 查找并包装为 ToolInstance，`build_definitions()` 委托 `registry.get_tool_definitions()`，`register_overlay()` 内部调 `registry.register()`。ContextBuilder / SkillTool / MCP 注入路径不动。

### 测试组织策略
- **D-05:** 新增测试跟随源码结构放置，不建新目录：
  - 类型体系测试 → `tests/matmaster/types/`（test_topology.py, test_tool_spec.py, test_tool_decision.py）
  - ToolCatalog → `tests/matmaster/tools/test_tool_catalog.py`
  - ToolRunner/InlineToolRunner → `tests/matmaster/core/test_tool_runner.py`
  - _run_items() / run_stream() → `tests/matmaster/core/`（与现有 test_agent_kernel.py 同目录）

### Spec 锁定
- **D-06:** 两份 spec（kernel-generator-first.md + tool-runtime-v2.md）的所有设计点确认锁定，可直接执行。包括：
  - `_KernelItem` / `_KernelState` / `_TerminalItem` 结构
  - `ToolExecutionContext` 字段（turn, max_turns, stop_event）
  - `InlineToolRunner` 实现（guard → pre_hook → asyncio.gather → post_hook，was_executed 区分）
  - Phase 1 Hook 并存策略（`_run_items()` yield final snapshot + Hook → Bus 继续运行）
  - 取消机制不变（threading.Event，InlineToolRunner 不检查 stop_event）
  - Phase 1 LLM 事件 yield 仅 final completed snapshot（非 segment-complete parity）
  - `_resolve_tool_definitions()` helper 抽取
  - ToolCatalog.version 机制

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 设计 Spec
- `docs/specs/2026-04-02-kernel-generator-first.md` — AgentKernel generator-first 改造完整设计，含三层接口、_KernelItem 结构、InlineToolRunner、AgentRuntimeSpec 扩展、Hook 并存策略、取消机制、Phase 衔接时序
- `docs/specs/2026-04-02-tool-runtime-v2.md` — Tool Runtime v2 架构设计，含 SessionCapabilities、RuntimeTopology、ToolPlane、ToolSpec、ToolBinding、ResourceClaim、ToolInstance、ToolCatalog、三层约束模型、ToolDecision、ToolResult 升级、调度模型、文件布局

### 现有核心代码
- `matmaster/core/agent.py` — 当前 AgentKernel 实现（699 行），_run_loop() 将改造为 _run_items() generator
- `matmaster/types/runtime.py` — AgentRuntimeSpec / KernelResult / KernelRunResult 定义，需扩展字段
- `matmaster/tools/tool_result.py` — 当前 ToolResult 定义（status/content/info），需升级为 status/content/payload/meta
- `matmaster/types/events.py` — 18 种事件类型，ToolResultEvent.info 字段需同步更新
- `matmaster/types/session.py` — Session Protocol，需增加 capabilities 属性
- `matmaster/tools/tool_registry.py` — ToolRegistry，Phase 1 作为 ToolCatalog 内部 facade
- `matmaster/core/guard_pipeline.py` — GuardPipeline，Phase 1 接口不变
- `matmaster/core/hooks.py` — Hook 调用函数（run_pre_tool_call, run_post_tool_call 等），InlineToolRunner 依赖

### 需求定义
- `.planning/REQUIREMENTS.md` — Phase 32 映射的 25 个 requirement（KGEN-01~05, TOBJ-01~08, TCAT-01~03, TRUN-01~02, TRUN-05, TCON-02, TRES-01, SPEC-01, TDEF-01, REGR-01, REGR-03）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/core/agent.py` L217-311: 工具执行链（guard → pre_hook → asyncio.gather → post_hook），将提取到 InlineToolRunner
- `matmaster/core/agent.py` L337-344: tool definitions 获取逻辑，将抽为 `_resolve_tool_definitions()` helper
- `matmaster/core/agent.py` L502-606: `_do_stream_llm()` 流式处理逻辑，Phase 1 不改动
- `matmaster/core/guard_pipeline.py`: GuardPipeline.evaluate() 接口，InlineToolRunner 直接复用
- `matmaster/core/hooks.py`: run_guard_blocked / run_pre_tool_call / run_post_tool_call 函数，InlineToolRunner 调用
- `matmaster/types/events.py`: ThoughtEvent / ResponseEvent / ToolCallEvent / ToolResultEvent / RunResultEvent，`_run_items()` 复用

### Established Patterns
- frozen Pydantic model 用于层间契约（PlaygroundContext, AgentRuntimeSpec）— 新类型遵循同样模式
- `@runtime_checkable Protocol` 用于接口定义（LLMProvider, Session, Guard, Hook）— ToolRunner 同样模式
- `TYPE_CHECKING + lazy import` 解决循环导入 — AgentRuntimeSpec 引用新类型时复用
- `dataclass(frozen=True)` 用于内核私有类型（KernelResult, AgentRuntime）— _KernelItem 等同样模式

### Integration Points
- `AgentRuntimeSpec` 是 Exp → Kernel 的唯一传递契约，新增字段直接在此处
- `matmaster/core/exp.py` Exp.build_runtime() 构造 spec，Phase 32 不改动此处（Phase 34 才接入）
- `matmaster/hooks/output_processor.py` 消费 result.info，需同步更新为 result.payload
- `matmaster/integration/event_payloads.py` 构造事件 payload，可能涉及 ToolResultEvent.info → payload 变更

</code_context>

<specifics>
## Specific Ideas

- 两份 spec 的代码示例可直接作为实现参考，伪代码已精确到行级
- InlineToolRunner 的 `was_executed` 标记和 post_hook 语义需严格遵循 spec 4.4 节说明
- `_resolve_tool_definitions()` 的 Phase 1/Phase 2 双路径设计需完整实现（即使 Phase 1 只走 registry 路径）

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 32-kernel-generator-tool-runtime-v2*
*Context gathered: 2026-04-02*
