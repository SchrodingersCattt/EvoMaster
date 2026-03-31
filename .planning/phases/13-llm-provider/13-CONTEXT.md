# Phase 13: LLM Provider 异步实现 - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

将 OpenAIProvider 从同步实现改造为 async，使用 AsyncOpenAI client，chat/chat_stream 成为真正的 async 方法。Provider 支持 async context manager 生命周期管理。顺带将 ContextCompactor._summarize() 改为 async（改动量极小）。

本阶段只改 matmaster/providers/ 和 ContextCompactor 的 LLM 调用点，不改 AgentKernel、Exp 生命周期、Hook、Tool 系统。

</domain>

<decisions>
## Implementation Decisions

### Client 生命周期管理
- **D-01:** OpenAIProvider 实现完整 async context manager。`__init__` 只存储参数不创建 client，`__aenter__` 创建 AsyncOpenAI + httpx.AsyncClient，`__aexit__` 关闭连接池。这建立全链路 async 的使用模式，后续 Phase 17-18 改造 Kernel/Exp 时直接遵循 `async with provider` 约定。
- **D-02:** AsyncOpenAI 构造函数本身是同步的（内部创建 httpx.AsyncClient 也是同步），但将创建延迟到 `__aenter__` 以保持语义一致性：provider 在进入 context 后才是可用状态。

### 下游调用者兼容
- **D-03:** ContextCompactor._summarize() 在 Phase 13 一并改为 async（只需将 `provider.chat()` 改为 `await provider.chat()`，改动量极小）。同时 ContextCompactor.compact() 等调用链需要相应改为 async。
- **D-04:** AgentKernel._call_llm() 使用临时同步桥接过渡（如 asyncio 事件循环桥接），Phase 17 正式改造时移除桥接代码。

### Factory 返回类型
- **D-05:** build_provider() 保持同步函数，返回未初始化的 OpenAIProvider 实例（此时 __init__ 只存参数，不涉及 I/O）。调用者通过 `async with provider` 管理 client 生命周期。Factory 职责不变：配置解析 + 对象构造。

### 测试策略
- **D-06:** 只迁移 provider 层单元测试：tests/matmaster/providers/test_openai_provider.py 和 test_llm_factory.py 改为 async 测试，mock AsyncOpenAI client。
- **D-07:** 新增 async context manager 生命周期测试（__aenter__ 创建 client、__aexit__ 关闭、未进入 context 时调用 chat 应报错）。
- **D-08:** 6 个集成测试（test_compaction_real_api, test_quota_pipeline, test_e2e_mat_master, test_bohrium_execution_contract, test_stream_timeout_retry, integration/test_llm_factory）留到 Phase 17-18 随 Kernel/Exp 一起迁移。

### Claude's Discretion
- httpx.AsyncClient 的超时配置细节（connect/read/write/pool 映射）
- chat_stream async generator 的具体实现方式（async def + yield vs AsyncStream wrapper）
- 异常映射是否需要调整（openai async 异常类型与 sync 一致）
- Kernel 临时同步桥接的具体实现方式（asyncio.run vs run_until_complete vs to_thread）
- ContextCompactor 调用链中需要改 async 的具体方法范围

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- LLMP-01, LLMP-02, LLMP-03 requirements 定义
- `.planning/ROADMAP.md` -- Phase 13 目标、依赖、成功标准
- `.planning/PROJECT.md` -- 核心决策（Protocol hard cut, 自底向上迁移, Guard sync）

### Phase 12 Context（前置阶段）
- `.planning/phases/12-protocol/12-CONTEXT.md` -- Protocol async 签名决策（D-01~D-08）

### Provider 实现（改造目标）
- `matmaster/providers/openai_provider.py` -- OpenAIProvider 当前同步实现（256 行）
- `matmaster/providers/llm_factory.py` -- build_provider() 工厂函数
- `matmaster/providers/__init__.py` -- 包导出

### Protocol 定义（已 async，Phase 12 完成）
- `matmaster/types/llm_provider.py` -- LLMProvider Protocol（async chat, async chat_stream）

### ContextCompactor（顺带改造）
- `matmaster/core/context_compactor.py` -- _summarize() 调用 provider.chat()（:272），compact() 调用链

### 下游调用者（了解但主体不改）
- `matmaster/core/agent.py` -- AgentKernel._call_llm()（:203）用临时桥接
- `matmaster/core/exp.py` -- Exp.assemble() 中 compaction provider 创建（:213-215）

### 测试文件（迁移范围）
- `tests/matmaster/providers/test_openai_provider.py` -- provider 单元测试（本阶段迁移）
- `tests/matmaster/providers/test_llm_factory.py` -- factory 单元测试（本阶段迁移）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 12 建立的 pytest-asyncio 基础设施 + async mock factories（tests/conftest.py）
- validate_async_protocol() helper 可验证 OpenAIProvider 实现是否满足 async Protocol

### Established Patterns
- 所有 Protocol 使用 `@runtime_checkable` 装饰器
- BuiltinTool 使用 Template Method 模式（execute → _execute）
- TYPE_CHECKING + lazy import 解决循环导入
- 异常统一映射为 LLMError(retryable=True/False)

### Integration Points
- OpenAIProvider 被 6 处引用：llm_factory, exp.py(compaction), 4 个集成测试
- ContextCompactor 接收 LLMProvider Protocol 实例，调用 chat()
- AgentKernel 通过 AgentRuntimeSpec.llm_provider 持有 provider 引用

</code_context>

<specifics>
## Specific Ideas

- AsyncOpenAI 构造函数是同步的，但 Phase 13 选择将创建延迟到 __aenter__ 以建立全链路 async context manager 约定
- 全链路改造目标下，"对下游改动大"不是顾虑——反正都要改，从这里开始建立正确模式
- ContextCompactor 改动极小（_summarize 加 await），顺带处理减少 Phase 17 工作量

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 13-llm-provider*
*Context gathered: 2026-03-27*
