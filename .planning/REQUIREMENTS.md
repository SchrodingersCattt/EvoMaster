# Requirements: MatMaster v2.2

**Defined:** 2026-04-02
**Core Value:** 三层抽象（playground→exp→agent）必须具有清晰、稳定、可测试的职责边界

## v2.2 Requirements

### Kernel Generator

- [x] **KGEN-01**: AgentKernel 提供 `_run_items()` 私有 AsyncGenerator 作为唯一执行路径，产出 `_KernelItem`（event + messages_delta + terminal）
- [x] **KGEN-02**: AgentKernel 提供 `run_stream()` 公开接口，yield `BusEvent`（复用 events.py），最后 yield `RunResultEvent`
- [x] **KGEN-03**: AgentKernel 的 `run()` 委托 `_run_items()` 收集 messages 和 terminal，返回 `KernelRunResult`，签名和行为与当前完全一致
- [x] **KGEN-04**: `_run_items()` 使用局部 `_KernelState` 管理循环状态（不挂在 self 上），保持 Kernel 无状态/并发安全
- [x] **KGEN-05**: `_run_items()` 在 LLM 调用返回后 yield final completed snapshot 事件（ResponseEvent / ThoughtEvent），Phase 1 不要求 segment-complete 语义

### Tool Runtime 对象模型

- [x] **TOBJ-01**: 定义 `SessionCapabilities` (frozen Pydantic model)，Session Protocol 增加 `capabilities` 属性自报告能力
- [x] **TOBJ-02**: 定义 `RuntimeTopology` (frozen Pydantic model)，包含 session_kind / control_root / workspace_root / active_planes / session_capabilities
- [x] **TOBJ-03**: 定义 `ToolPlane` 枚举（SESSION_SHELL / SESSION_FS / CONTROL_PLANE / EXTERNAL_SERVICE）
- [x] **TOBJ-04**: 定义 `ToolSpec` (frozen Pydantic model)，表达工具逻辑语义（tool_name / description / args_schema / source / capabilities / effect_level / exposed_to_model / fast_path_eligible）
- [x] **TOBJ-05**: 定义 `ResourceClaim` (frozen Pydantic model)，支持 exclusive / shared_read / counted 三种 mode
- [x] **TOBJ-06**: 定义 `ToolBinding` (frozen Pydantic model)，表达工具在当前环境下的执行语义（plane / resource_claims / state_mode / stop_mode）
- [x] **TOBJ-07**: 定义 `ToolInstance` (frozen dataclass)，组合 ToolSpec + ToolBinding + tool_executor
- [x] **TOBJ-08**: 定义 `ToolDecision` (frozen Pydantic model)，统一 allow/deny 决策（decision / reason / guidance）

### Tool Catalog

- [x] **TCAT-01**: 实现 `ToolCatalog`（base + overlay 两层结构），base 在 build_runtime() 编译时不可变，overlay 承载 skill 触发的 MCP 工具懒注入
- [x] **TCAT-02**: `ToolCatalog` 维护 `version` 属性，overlay 变更时递增，Kernel 每轮比对 version 决定是否刷新 tool_definitions
- [x] **TCAT-03**: `ToolCatalog` Phase 1 内部持有 ToolRegistry 实例作为兼容 facade，不改动 ContextBuilder / SkillTool / MCP 注入路径

### Tool Runner + Scheduler

- [x] **TRUN-01**: 定义 `ToolRunner` Protocol（`@runtime_checkable`），接口为 `execute_batch(tool_calls, ctx, on_result) -> list[(ToolCallData, ToolResult)]`
- [x] **TRUN-02**: 实现 `InlineToolRunner` 作为 Phase 1 过渡，包装当前 agent.py 的 guard → pre_hook → execute → post_hook 逻辑
- [ ] **TRUN-03**: 实现完整 `ToolRunner`，执行链为 ToolCatalog 查找 → StructuralValidation → RunStateGuard → CapabilityPolicy → fast path 判定 → ToolScheduler → executor → 释放
- [ ] **TRUN-04**: 实现 `ToolScheduler`，基于 ResourceClaim 调度（exclusive 互斥 / shared_read 并发 / counted 信号量），支持 fast path 跳过
- [x] **TRUN-05**: Kernel 通过 `spec.tool_runner` 获取 ToolRunner，Phase 1 回退到 InlineToolRunner

### 三层约束模型

- [ ] **TCON-01**: 实现 `StructuralValidation`（无状态），负责 args_schema 校验 / 路径规范化 / plane 启用检查 / session_capabilities 匹配
- [x] **TCON-02**: `RunStateGuard` 保持现有 GuardPipeline 接口，Phase 1 不扩展 GuardContext
- [ ] **TCON-03**: 实现 `CapabilityPolicy` Protocol，Phase 1 处理 effect_level 约束和 plane/capability 匹配

### ToolResult 升级

- [x] **TRES-01**: `ToolResult` 升级为 status + content + payload + meta 四字段，payload 替代原 info 的结构化数据角色

### AgentRuntimeSpec 扩展

- [x] **SPEC-01**: `AgentRuntimeSpec` 新增 tool_runner / tool_catalog / runtime_topology / capability_policy / structural_validation 可选字段

### Tool Definitions 解析

- [x] **TDEF-01**: 抽出 `_resolve_tool_definitions()` helper，Phase 1 回退到 tool_registry，Phase 2 自动切到 tool_catalog

### Exp/Service 层接入

- [ ] **ESIN-01**: `Exp` 新增 `run_stream()`，透传 Kernel generator
- [ ] **ESIN-02**: `AgentRunService` 新增 `run_agent_stream()`，消费 generator 事件
- [ ] **ESIN-03**: `_do_stream_llm()` 改造为子 generator `_stream_llm_items()`，支持逐 chunk yield，达到与 EventEmitterHook 完全一致的 segment-complete 语义

### Hook 退役

- [ ] **HRET-01**: `_run_items()` 产出等价 ThoughtEvent / ResponseEvent / ToolCallEvent / ToolResultEvent，替代 EventEmitterHook
- [ ] **HRET-02**: `_run_items()` 在 tool_calls 轮次 yield AssistantStateEvent，替代 AssistantStateHook
- [ ] **HRET-03**: `_run_items()` 产出等价 SkillHitEvent，替代 SkillHitHook
- [ ] **HRET-04**: OutputProcessorHook 功能迁移到 ToolRunner post-execute 阶段或消费侧 event transformer
- [ ] **HRET-05**: ContextCompactor bus 依赖改为通过 Kernel yield ContextCompactionEvent
- [ ] **HRET-06**: 全部 5 个 Hook 退役后，移除 Hook → Bus 间接事件路径

### 约束迁移

- [ ] **CMIG-01**: 扩展 GuardContext 增加 ReadTracker，将 read-before-modify 检查从 WriteTool/EditTool 迁入 RunStateGuard
- [ ] **CMIG-02**: 将 bash_tool 的 `_is_dangerous_command` 迁入 CapabilityPolicy
- [ ] **CMIG-03**: ToolBinding 的 state_mode / stop_mode 字段启用，Scheduler 根据 SessionCapabilities 调整策略
- [ ] **CMIG-04**: ToolRegistry 降级为纯存储层，ToolCatalog 接管所有上层消费接口

### 去总线化

- [ ] **DBUS-01**: 审计 MessageBus + EventRouter 的全部消费者（SSEHandler / PersistenceHandler / DevShell EventLogger / 其他）
- [ ] **DBUS-02**: 设计并实现消费侧 async fanout 替代方案，确保 SSE 先发、持久化不阻塞 token 流
- [ ] **DBUS-03**: 移除 MessageBus + EventRouter，generator 事件直连消费者

### 高级调度

- [ ] **ASCH-01**: ToolScheduler 根据 SessionCapabilities 动态调整并发策略（如 persistent shell 下支持 shell 并发）

### 回归兼容

- [x] **REGR-01**: 全量现有 `kernel.run()` 测试（50+）零修改通过
- [ ] **REGR-02**: Exp.run() 和 AgentRunService.run_agent() 行为不变
- [x] **REGR-03**: 工具内部现有安全检查（bash 危险命令、read-before-modify）在约束迁移完成前保持不动

## Future Requirements

### 高级特性（v2.3+）

- **FUTR-01**: SubAgent spawn 切换到 kernel.run_stream() 实现实时子 agent 事件转发
- **FUTR-02**: ConfirmationHook 双向流在 generator 模型中的替代方案
- **FUTR-03**: DevShell 消费 run_stream() 替代当前 run() + Bus 路径

## Out of Scope

| Feature | Reason |
|---------|--------|
| 前端界面与工具展示 | Tool Runtime v2 spec 明确排除 |
| 交互式权限弹窗与人工批准流程 | Tool Runtime v2 spec 明确排除 |
| 服务层账号、租户、计费逻辑 | Tool Runtime v2 spec 明确排除 |
| Provider 层协议重写 | 保持 OpenAI 兼容 tool definitions |
| 新建事件类型 | 复用 events.py 现有 18 种类型 |
| 改动取消机制 | 继续使用 threading.Event（Redis 驱动） |
| Hook Protocol 接口变更 | Phase 1 不改动，Phase 2 通过退役替代 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| KGEN-01 | Phase 32 | Complete |
| KGEN-02 | Phase 32 | Complete |
| KGEN-03 | Phase 32 | Complete |
| KGEN-04 | Phase 32 | Complete |
| KGEN-05 | Phase 32 | Complete |
| TOBJ-01 | Phase 32 | Complete (32-01) |
| TOBJ-02 | Phase 32 | Complete (32-01) |
| TOBJ-03 | Phase 32 | Complete (32-01) |
| TOBJ-04 | Phase 32 | Complete (32-01) |
| TOBJ-05 | Phase 32 | Complete (32-01) |
| TOBJ-06 | Phase 32 | Complete (32-01) |
| TOBJ-07 | Phase 32 | Complete (32-01) |
| TOBJ-08 | Phase 32 | Complete (32-01) |
| TCAT-01 | Phase 32 | Complete |
| TCAT-02 | Phase 32 | Complete |
| TCAT-03 | Phase 32 | Complete |
| TRUN-01 | Phase 32 | Complete |
| TRUN-02 | Phase 32 | Complete |
| TRUN-03 | Phase 33 | Pending |
| TRUN-04 | Phase 33 | Pending |
| TRUN-05 | Phase 32 | Complete |
| TCON-01 | Phase 33 | Pending |
| TCON-02 | Phase 32 | Complete |
| TCON-03 | Phase 33 | Pending |
| TRES-01 | Phase 32 | Complete (32-01) |
| SPEC-01 | Phase 32 | Complete |
| TDEF-01 | Phase 32 | Complete |
| ESIN-01 | Phase 34 | Pending |
| ESIN-02 | Phase 34 | Pending |
| ESIN-03 | Phase 34 | Pending |
| HRET-01 | Phase 34 | Pending |
| HRET-02 | Phase 34 | Pending |
| HRET-03 | Phase 34 | Pending |
| HRET-04 | Phase 34 | Pending |
| HRET-05 | Phase 34 | Pending |
| HRET-06 | Phase 34 | Pending |
| CMIG-01 | Phase 35 | Pending |
| CMIG-02 | Phase 35 | Pending |
| CMIG-03 | Phase 35 | Pending |
| CMIG-04 | Phase 35 | Pending |
| DBUS-01 | Phase 36 | Pending |
| DBUS-02 | Phase 36 | Pending |
| DBUS-03 | Phase 36 | Pending |
| ASCH-01 | Phase 36 | Pending |
| REGR-01 | Phase 32 | Complete |
| REGR-02 | Phase 34 | Pending |
| REGR-03 | Phase 32 | Complete |

**Coverage:**
- v2.2 requirements: 46 total
- Mapped to phases: 46
- Unmapped: 0

---
*Requirements defined: 2026-04-02*
*Last updated: 2026-04-02 after 32-01 completion (9 requirements)*
