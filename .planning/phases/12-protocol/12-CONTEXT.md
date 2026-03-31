# Phase 12: Protocol 层 + 测试基础设施 - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

将所有 async Protocol 合约明确定义（签名 + 返回类型），建立 pytest-asyncio 测试基础设施（conftest + async mock factories），实现 async validation helper 用于运行时和测试中的签名检查。

本阶段只改 Protocol/ABC 定义，不改具体实现。现有实现（OpenAIProvider, 12 BuiltinTools, 5 Hooks）保持同步，在 Phase 13-18 逐步改造。

</domain>

<decisions>
## Implementation Decisions

### 过渡策略
- **D-01:** Phase 12 只改 Protocol 定义和 BuiltinTool ABC 签名。现有实现保持 sync，不添加 async 壳。后续 Phase 13-18 逐步将实现改为真正的 async。测试用 async mock 验证新 Protocol。

### Protocol 覆盖范围
- **D-02:** 改 6 个 Protocol：LLMProvider, Tool, Hook, Guard（REQUIREMENTS 定义） + EventHandler, ReplyQueueLike（Phase 15/16 需要，提前改签名省事）。WorkerRegistry 属于 src/ 层，不在 v2.0 范围，不改。
- **D-03:** Guard Protocol 的 evaluate() 保持同步不变（明确决策：纯计算无 I/O，async 增加开销无收益）。

### chat_with_retry 处理
- **D-04:** chat_with_retry() 从 LLMProvider Protocol 移除，同时从 OpenAIProvider 实现中也删除。重试逻辑已在 Kernel._call_llm() 中，Provider 级别的 retry 是冗余的。

### Tool 签名
- **D-05:** Tool Protocol 保持 execute 命名（不改为 run），REQUIREMENTS 中 PROT-02 的 run() 是笔误。Tool Protocol.execute() 改为 async def（合约声明）。BuiltinTool ABC 的 _execute() abstractmethod 改为 async def。BuiltinTool.execute() 具体方法体保持 sync，在 Phase 14 随 ToolRegistry 一起改造（D-01 优先：Phase 12 不改运行热路径）。

### 返回类型
- **D-06:** Protocol 签名变更包括返回类型。LLMProvider.chat_stream() 返回类型从 Iterator[StreamChunk] 改为 AsyncIterator[StreamChunk]。这是合约定义的一部分，在 Phase 12 一并处理。

### Validation Helper
- **D-07:** 实现实例级 async validation helper：validate_async_protocol(obj, protocol_cls)。给定实例和 Protocol 类，检查实例的每个 Protocol 方法是否是 async def（通过 inspect.iscoroutinefunction）。定位：测试工具 + Exp.assemble() 组装时的早期检测。

### 测试基础设施
- **D-08:** 创建 tests/conftest.py 提供 async mock factories（mock async LLMProvider, mock async Tool, mock async Hook），供后续阶段直接复用。加上 async validation helper 自身的测试。pytest.ini 已有 asyncio_mode=auto，确认生效即可。

### Claude's Discretion
- async mock factories 的具体实现细节（AsyncMock vs 手写 async def）
- validation helper 的错误消息格式
- conftest.py 中 fixture scope 选择
- Hook Protocol 各方法的返回类型是否需要调整（当前大部分返回 None）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- PROT-01~05, TEST-01 requirements 定义
- `.planning/ROADMAP.md` -- Phase 12 目标、依赖、成功标准
- `.planning/PROJECT.md` -- 核心决策（Protocol hard cut, Guard sync, 自底向上迁移）

### Protocol 定义文件（改造目标）
- `matmaster/types/llm_provider.py` -- LLMProvider Protocol（chat, chat_with_retry, chat_stream）
- `matmaster/tools/tool_registry.py` -- Tool Protocol（execute, name, description, json_schema）
- `matmaster/core/hooks.py` -- Hook Protocol（7 个方法）+ run_pre_tool_call 等 helper
- `matmaster/types/guards.py` -- Guard Protocol（evaluate 保持 sync）
- `matmaster/integration/event_router.py` -- EventHandler Protocol（handle）
- `matmaster/hooks/confirmation.py` -- ReplyQueueLike Protocol（put_content, put_cancel, get）

### BuiltinTool ABC
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC（execute + _execute，两个都改 async）

### 现有实现（了解但不改动）
- `matmaster/providers/openai_provider.py` -- OpenAIProvider（chat_with_retry 将在此阶段删除）
- `matmaster/tools/builtin/` -- 12 个 BuiltinTool 实现（Phase 14 改造）
- `matmaster/hooks/` -- 5 个 Hook 实现（Phase 15 改造）

### 测试基础设施
- `pytest.ini` -- 已有 asyncio_mode=auto 配置
- `tests/` -- 863+ 现有测试（本阶段不迁移，Phase 17 随实现同步迁移）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pytest.ini` 已配置 asyncio_mode=auto，async def test 自动识别
- 现有测试使用 SimpleNamespace 和 MagicMock 构建 mock，可作为 async mock factories 的基础

### Established Patterns
- 所有 Protocol 使用 `@runtime_checkable` 装饰器
- Pydantic BaseModel 用于所有数据契约（PlaygroundContext, AgentRuntimeSpec, AgentEvent）
- BuiltinTool 使用 Template Method 模式：execute()（公共 + 异常处理）调用 _execute()（抽象 + 具体逻辑）
- TYPE_CHECKING + lazy import 解决循环导入

### Integration Points
- Protocol 签名变更影响所有后续阶段（Phase 13-18）的实现
- BuiltinTool ABC 签名变更影响 12 个具体 Tool 子类
- Hook Protocol 签名变更影响 5 个具体 Hook + run_* helper 函数
- validation helper 将被 Exp.assemble() 使用（Phase 18）

</code_context>

<specifics>
## Specific Ideas

- REQUIREMENTS 中 PROT-02 的 "run()" 是笔误，实际代码中 Tool Protocol 使用 "execute()"
- chat_with_retry 同时从 Protocol 和 OpenAIProvider 实现中删除（不保留为内部方法）
- validation helper 定位为实例级检查器，不是类装饰器或纯测试工具

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 12-protocol*
*Context gathered: 2026-03-26*
