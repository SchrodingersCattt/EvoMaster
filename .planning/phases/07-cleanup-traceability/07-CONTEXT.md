# Phase 7: Cleanup and Traceability - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

清理 QueueBridge 冗余实现，重组 matmaster 目录结构（消除单文件包、按职责合并），修正追踪文档（REQUIREMENTS.md 注释 + 里程碑审计报告更新），确保 v1 milestone 审计通过。

</domain>

<decisions>
## Implementation Decisions

### QueueBridge 清理
- **D-01:** 完全删除 QueueBridge — 删除 `matmaster/bus/bridge.py`（实现）、`tests/matmaster/bus/test_queue_bridge.py`（26 个测试）、`matmaster/bus/__init__.py` 中的导出
- **D-02:** 清理 `bus/queue.py` docstring 中对 QueueBridge 的引用
- **D-03:** 设计依据 — SSEHandler 通过 `event.model_dump()` + EventRouter 多 handler 组合模式取代了 QueueBridge 的 16 分支手动映射 + 单消费者拉模式。SSEHandler 扩展性更优（新事件零改动、多消费者、可组合）

### matmaster 目录重组
- **D-04:** 重组方案如下：

| 旧路径 | 新路径 | 说明 |
|--------|--------|------|
| `engine/agent.py` | `core/agent.py` | AgentKernel |
| `engine/guard_pipeline.py` | `core/guard_pipeline.py` | GuardPipeline |
| `engine/hooks.py` | `core/hooks.py` | Hook Protocol + EventEmitterHook |
| `engine/types.py` | `types/messages.py` | Message 类型（SystemMessage/UserMessage/AssistantMessage/ToolMessage） |
| `assembly/exp.py` | `core/exp.py` | Exp base class |
| `assembly/direct_exp.py` | `core/direct_exp.py` | DirectExp |
| `assembly/context_builder.py` | `core/context_builder.py` | ContextBuilder |
| `assembly/tool_registry.py` | `tools/tool_registry.py` | ToolRegistry |
| `assembly/evomaster_tool_adapter.py` | `tools/evomaster_tool_adapter.py` | EvoToolAdapter |
| `assembly/worker_registry.py` | `types/worker_registry.py` | WorkerRegistry Protocol |
| `assembly/guards.py` | 删除 | 只剩 8 行 docstring |
| `bus/queue.py` | `core/bus.py` | MessageBus（有状态运行时组件） |
| `bus/bridge.py` | 删除 | QueueBridge（D-01） |
| `playground/playground.py` | `core/playground.py` | Playground |

- **D-05:** 保持不变的包：`hooks/`（业务 hooks）、`integration/`（service 集成）、`providers/`（LLM provider）、`types/`（契约/Protocol 定义）
- **D-06:** 旧 import 路径直接断掉，全量更新所有 import（matmaster 内部 + tests/ + src/services/）。不加 re-export 兼容层，无外部消费者
- **D-07:** 测试目录镜像源码目录。`tests/matmaster/` 下的 assembly/、bus/、engine/、playground/ 合并到对应的 core/、tools/、types/

### 重组后目标结构

```
matmaster/
├── core/                  # 运行时核心（kernel + exp + playground + bus）
│   ├── agent.py
│   ├── bus.py
│   ├── context_builder.py
│   ├── direct_exp.py
│   ├── exp.py
│   ├── guard_pipeline.py
│   ├── hooks.py
│   └── playground.py
├── types/                 # 契约 + Protocol + 数据类型
│   ├── context.py
│   ├── events.py
│   ├── guards.py
│   ├── llm_provider.py
│   ├── messages.py
│   ├── runtime.py
│   └── worker_registry.py
├── tools/                 # 工具注册 + 适配
│   ├── tool_registry.py
│   └── evomaster_tool_adapter.py
├── hooks/                 # 业务 hooks（不变）
│   ├── assistant_state.py
│   ├── confirmation.py
│   ├── output_processor.py
│   └── skill_hit.py
├── integration/           # service 集成（不变）
│   ├── bohrium_setup.py
│   ├── event_router.py
│   └── workspace_handler.py
└── providers/             # LLM provider（不变）
    └── openai_provider.py
```

### EBUS-02 追踪更新
- **D-08:** REQUIREMENTS.md 中 EBUS-02 保留原始描述，加注 "实现已由 SSEHandler (matmaster/integration/event_router.py) 取代，QueueBridge 已清理"
- **D-09:** v1-MILESTONE-AUDIT.md 更新 EBUS-02 gap 为 resolved，更新 tech_debt 计数
- **D-10:** REQUIREMENTS.md checkbox 和 traceability 表已在 Phase 5/6 期间修正完毕，无需额外改动

### 附带清理
- **D-11:** commit 4 个未 tracked 的 .planning 文件（05-CONTEXT.md、MIGRATION-MAPPING.md、06-CONTEXT.md、06-VALIDATION.md）
- **D-12:** Phase 7 执行完成后补写 07-VERIFICATION.md

### Claude's Discretion
- 目录重组的具体执行顺序（先移动文件 vs 先更新 import）
- `__init__.py` 包导出的具体调整
- 测试文件合并时的具体重命名策略（如 test_queue.py → test_bus.py）
- import 更新的批量替换方式

</decisions>

<specifics>
## Specific Ideas

- QueueBridge 删除的设计依据：SSEHandler 的 `event.model_dump()` + EventRouter 多 handler 组合模式在扩展性上全面优于 QueueBridge 的 16 分支 isinstance 手动映射 + 单消费者拉模式。新事件类型零改动、多消费者天然支持、handler 可组合
- 目录重组的核心理念：消除单文件包（playground/、bus/），按职责边界重新分组（运行时核心 vs 类型契约 vs 工具 vs 业务 hook vs 集成 vs provider）
- assembly/ 拆分逻辑：exp/direct_exp/context_builder 是运行时核心组件（装配并执行），归 core/；tool_registry/adapter 是工具领域，归 tools/；worker_registry 是 Protocol 定义，归 types/；guards.py 是空壳，删除

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` -- 项目愿景、核心价值、Key Decisions 表
- `.planning/REQUIREMENTS.md` -- EBUS-02 需求定义及追踪表
- `.planning/ROADMAP.md` -- Phase 7 目标、成功标准

### 里程碑审计
- `.planning/v1-MILESTONE-AUDIT.md` -- EBUS-02 tech debt 记录（需更新为 resolved）

### QueueBridge（删除目标）
- `matmaster/bus/bridge.py` -- QueueBridge 实现（待删除）
- `tests/matmaster/bus/test_queue_bridge.py` -- 26 个测试（待删除）
- `matmaster/bus/__init__.py` -- QueueBridge 导出（待清理）

### SSEHandler（QueueBridge 的替代）
- `matmaster/integration/event_router.py` -- SSEHandler 定义 + EventRouter 多 handler 分发
- `src/services/agent_run_service.py` -- SSEHandler 生产调用方

### 目录重组（全部需要移动/更新 import 的文件）
- `matmaster/engine/` -- agent.py, guard_pipeline.py, hooks.py, types.py（→ core/ 和 types/）
- `matmaster/assembly/` -- exp.py, direct_exp.py, context_builder.py, tool_registry.py, evomaster_tool_adapter.py, worker_registry.py, guards.py（→ core/, tools/, types/, 删除）
- `matmaster/bus/queue.py` -- MessageBus（→ core/bus.py）
- `matmaster/playground/playground.py` -- Playground（→ core/playground.py）
- `src/services/agent_run_service.py` -- 包含大量 matmaster import 需要更新
- `src/services/worker_registry_adapter.py` -- import matmaster.assembly.worker_registry

### Phase 1-6 上下文
- `.planning/phases/06-service-layer-wiring/06-CONTEXT.md` -- 最近一个 phase 的决策上下文
- `.planning/phases/06-service-layer-wiring/06-VERIFICATION.md` -- Phase 6 验证报告

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/integration/event_router.py` SSEHandler -- QueueBridge 的替代，已在生产使用
- 现有 `__init__.py` 包导出模式 -- 重组后需要更新但模式一致

### Established Patterns
- Pydantic frozen model 用于不可变契约（types/ 下所有文件）
- `@runtime_checkable` Protocol 用于接口定义（guards.py, llm_provider.py, worker_registry.py）
- 延迟导入模式（TYPE_CHECKING guard + 运行时 lazy import）用于解决循环依赖 -- 重组后可能需要重新评估

### Integration Points
- `src/services/agent_run_service.py` -- 最密集的 matmaster import 消费者，重组后 import 路径全部需要更新
- `src/services/worker_registry_adapter.py` -- import matmaster.assembly.worker_registry → matmaster.types.worker_registry
- `matmaster/hooks/` 下 4 个文件 -- import matmaster.engine.hooks/types → matmaster.core.hooks / matmaster.types.messages
- `matmaster/integration/` 下 3 个文件 -- import matmaster.types.events, matmaster.bus 等需要更新

</code_context>

<deferred>
## Deferred Ideas

- PlannerExp 完全重写 -- 设计理念与重构方向冲突，独立排期
- x_master playground 迁移 -- 优先 mat_master 和 minimal
- nanobot 风格 tools 重写（脱离 EvoMaster BaseTool 体系）-- 长期方向，当前 EvoToolAdapter 足够
- 旧代码清理（evomaster/、playground/mat_master/ 中的废弃模块）-- v2 milestone

</deferred>

---

*Phase: 07-cleanup-traceability*
*Context gathered: 2026-03-22*
