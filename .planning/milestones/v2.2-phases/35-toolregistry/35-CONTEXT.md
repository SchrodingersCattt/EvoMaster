# Phase 35: 约束迁移 + ToolRegistry 降级 - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

工具安全检查从工具内部分散逻辑统一迁入三层约束模型：read-before-modify 检查从 WriteTool/EditTool 迁入 RunStateGuard，bash 危险命令 + Python 内容检查从 BashTool 迁入 CapabilityPolicy。工具内部安全检查代码完全删除，工具变为纯执行层。ToolBinding 的 state_mode/stop_mode 字段按 spec 纠正枚举值并启用（ToolCompiler 填充 + Scheduler 消费）。ToolRegistry 降级为纯存储层（删除 execute/get_tool_definitions 等方法），AgentRuntimeSpec.tool_registry 字段删除，Kernel legacy fallback 路径清理。ContextBuilder 工具枚举段删除，改为通用说明。ToolCatalog 成为唯一上层消费接口。

</domain>

<decisions>
## Implementation Decisions

### 约束迁移策略
- **D-01:** read-before-modify 检查完全从 WriteTool/EditTool 内部删除。ReadTracker 仅通过 GuardContext 注入 RunStateGuard。工具变纯粹——WriteTool 只写，EditTool 只编辑。符合 spec 设计意图：安全约束统一在三层模型，不在工具内部。
- **D-02:** ReadTracker 实例在 Exp.build_runtime() 中创建，注入 GuardPipeline 的 RunStateGuard。ReadTool 仍持有 tracker 引用调用 mark_read()——它是状态产生者不是检查者。Guard 层作为状态消费者检查 has_been_read()。符合 spec §6.10 "read_tracker 注入 GuardContext"。
- **D-03:** bash 危险命令检查 (is_dangerous_bash_command) 和 Python 内容检查 (is_dangerous_python_content) 全部从 bash_tool.py 迁入 CapabilityPolicy。bash_tool.py 变为纯执行层，零安全策略。CapabilityPolicy 根据 tool_name 分发不同检查逻辑。

### ToolRegistry 降级
- **D-04:** 激进删除。ToolRegistry 删除 execute()、get_tool_definitions()、get_tools_by_source() 方法。只保留 register()、all_tools、__contains__、__len__ 等纯存储接口。断点干净。
- **D-05:** AgentRuntimeSpec.tool_registry 字段删除。Kernel 仅通过 spec.tool_runner + spec.tool_catalog 消费工具。Exp.build_runtime() 不再把 registry 注入 spec。Kernel 中的 registry.execute() legacy fallback 路径同步删除。
- **D-06:** Kernel 中所有直接调用 spec.tool_registry.execute() 和 spec.tool_registry.get_tool_definitions() 的代码路径（agent.py L323, L500, L688, L856）全部删除。_resolve_tool_definitions() 不再有 registry fallback——必须走 tool_catalog。

### ContextBuilder 处置
- **D-07:** 按 spec §6.9 建议，移除 _build_tools() 中的工具逐行枚举，改为通用说明（如"使用 function calling 中声明的工具"）。消除 system prompt 与 tool_definitions 的不一致风险，尤其是 MCP overlay 动态注入后。ContextBuilder 不再依赖 tool_registry。

### ToolBinding state_mode/stop_mode
- **D-08:** 按 spec §6.6 纠正枚举值。state_mode 从 `str = "stateless"` 改为 `Literal["stateless", "persistent"] = "stateless"`。stop_mode 从 `str = "immediate"` 改为 `Literal["cancellable", "best_effort", "non_cancellable"] = "cancellable"`。当前代码中的 turn_scoped/session_scoped/immediate/graceful/detached 值未被任何代码消费，纠正代价为零。
- **D-09:** Phase 35 一步到位完成 CMIG-03：(1) ToolCompiler 根据工具元数据填充 state_mode/stop_mode，(2) Scheduler 根据 stop_mode 调整取消策略。当前 Scheduler 已有 stop_event 路径，改动量可控。

### Claude's Discretion
- RunStateGuard 的具体实现结构（是新增独立 Guard 类还是扩展 LoopDetectionGuard）
- CapabilityPolicy 中 bash/python 检查的具体分发逻辑
- ToolCompiler 中各内建工具的 state_mode/stop_mode 具体取值映射
- Scheduler 对 best_effort/non_cancellable 的具体行为差异实现
- Exp.run()/run_stream() 中 stop_event 注入路径的调整（不再走 tool_registry.all_tools）
- 测试文件的具体组织方式

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 核心设计
- `docs/specs/2026-04-02-tool-runtime-v2.md` — Tool Runtime v2 完整架构设计。Phase 35 重点：§6.6 ToolBinding（state_mode/stop_mode 枚举值定义）、§6.9 ToolCatalog（system prompt 策略）、§6.10 三层约束模型（RunStateGuard Phase 2 扩展 + CapabilityPolicy Phase 2 扩展）、§8.2 内建工具 ResourceClaim 表
- `docs/plans/2026-04-02-v2.2-phase2-advancement.md` — v2.2 Phase 2 三波次推进设计。Phase 35 对应 Task 4 (Wave C)。包含约束迁移 4 步骤 + 验证命令 + 文件清单

### Phase 32-34 产出（已实现的基础设施）
- `matmaster/core/tool_runner.py` — FullToolRunner 七步执行链，Phase 35 的约束迁移在此链中的 Layer B (RunStateGuard) 和 Layer C (CapabilityPolicy) 生效
- `matmaster/tools/tool_catalog.py` — ToolCatalog，Phase 35 后成为唯一上层消费接口
- `matmaster/tools/tool_compiler.py` — ToolCompiler，Phase 35 需扩展填充 state_mode/stop_mode
- `matmaster/core/capability_policy.py` — DefaultCapabilityPolicy，Phase 35 扩展承接 bash/python 安全检查
- `matmaster/core/guard_pipeline.py` — GuardPipeline + LoopDetectionGuard，Phase 35 新增 RunStateGuard
- `matmaster/core/tool_scheduler.py` — ToolScheduler，Phase 35 根据 stop_mode 调整取消策略
- `matmaster/core/structural_validation.py` — StructuralValidation（无需改动，仅参考）

### 迁移源文件
- `matmaster/tools/builtin/read_tracker.py` — ReadTracker 定义（mark_read / has_been_read / clear）
- `matmaster/tools/builtin/write_tool.py` — WriteTool，L84-89 read-before-modify 检查（待删除）
- `matmaster/tools/builtin/edit_tool.py` — EditTool，L108-111 read-before-modify 检查（待删除）
- `matmaster/tools/builtin/bash_tool.py` — BashTool，L21-84 is_dangerous_bash_command + is_dangerous_python_content（待迁移到 CapabilityPolicy）

### 降级目标文件
- `matmaster/tools/tool_registry.py` — ToolRegistry，删除 execute/get_tool_definitions/get_tools_by_source
- `matmaster/types/runtime.py` — AgentRuntimeSpec，删除 tool_registry 字段
- `matmaster/core/agent.py` — AgentKernel，删除 legacy registry fallback 路径（L323, L500, L688, L856）
- `matmaster/core/exp.py` — Exp.build_runtime()，不再注入 registry 到 spec；stop_event 注入路径调整
- `matmaster/core/context_builder.py` — ContextBuilder，_build_tools() 改为通用说明

### 类型定义
- `matmaster/types/tool_spec.py` — ToolBinding state_mode/stop_mode 枚举值纠正
- `matmaster/types/guards.py` — GuardContext 扩展 read_tracker 字段

### 需求定义
- `.planning/REQUIREMENTS.md` — Phase 35 映射的 5 个 requirement（CMIG-01~05）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/builtin/read_tracker.py`: ReadTracker 类（mark_read/has_been_read/clear），无需修改，只需调整注入路径
- `matmaster/tools/builtin/bash_tool.py` L21-84: is_dangerous_bash_command/is_dangerous_python_content 的正则模式集合，迁移时可直接搬到 CapabilityPolicy
- `matmaster/core/guard_pipeline.py` L72-100: GuardPipeline.evaluate() + LoopDetectionGuard，RunStateGuard 遵循同样的 Guard Protocol
- `matmaster/core/capability_policy.py` L46-114: DefaultCapabilityPolicy.evaluate()，现有框架直接扩展
- `matmaster/core/tool_scheduler.py`: Scheduler 已有 stop_event 路径，stop_mode 消费可直接在此基础上扩展
- `matmaster/tools/tool_compiler.py` L89-93: ToolBinding 构造逻辑，需扩展 state_mode/stop_mode 设置

### Established Patterns
- frozen Pydantic model 层间契约（GuardContext 是 dataclass，需判断是否改为 Pydantic 或保持 dataclass）
- @runtime_checkable Protocol 接口（Guard Protocol 已定义，RunStateGuard 遵循）
- CapabilityPolicy Protocol 已定义，DefaultCapabilityPolicy 是具体实现
- ToolDecision 作为 Layer B/C 的统一返回类型

### Integration Points
- Exp._init_builtin_tools() 是 ReadTracker 的当前创建点，迁移后需在 build_runtime() 中创建
- Exp.run()/run_stream() 中 `spec.tool_registry.all_tools` 的 stop_event 注入需改走 ToolCatalog
- ContextBuilder.build() 的 `registry` 参数需调整或移除

</code_context>

<specifics>
## Specific Ideas

- Advancement plan Task 4 的 4 个 Step 是执行参考，但 Step 3 (state_mode/stop_mode) 和 Step 4 (ToolRegistry 降级) 可以调整顺序
- spec §6.6 的 stop_mode 枚举值 (cancellable/best_effort/non_cancellable) 比当前代码中的 (immediate/graceful/detached) 语义更精确——cancellable 表示可取消，best_effort 表示尝试取消但不保证，non_cancellable 表示不可取消
- spec §6.10 RunStateGuard Phase 2 目标态明确给出了扩展后的 GuardContext 结构
- CapabilityPolicy Phase 2 新增职责明确：危险命令拦截 + 跨工具统一的确定性安全约束
- ContextBuilder._build_tools() 当前约 7 行代码，删除改为通用说明后会更短

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 35-toolregistry*
*Context gathered: 2026-04-03*
