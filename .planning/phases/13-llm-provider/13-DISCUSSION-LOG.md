# Phase 13: LLM Provider 异步实现 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 13-llm-provider
**Areas discussed:** Client 生命周期管理, 下游调用者兼容, llm_factory 返回类型, 测试策略

---

## Client 生命周期管理

| Option | Description | Selected |
|--------|-------------|----------|
| async close() 方法 | __init__ 创建 AsyncOpenAI，新增 async close()。可同时实现 __aenter__/__aexit__ 作为便利接口 | |
| 纯 async context manager | __init__ 只存参数，__aenter__ 创建 client，__aexit__ 关闭 | ✓ |
| You decide | Claude 决定 | |

**User's choice:** 纯 async context manager
**Notes:** 用户指出全链路都会改造成 async，从 Phase 13 开始建立 __aenter__/__aexit__ 模式更合理。"对下游改动大"在全链路 async 前提下不是顾虑。Claude 最初推荐 async close() 但用户的论点更有说服力。

---

## 下游调用者兼容

| Option | Description | Selected |
|--------|-------------|----------|
| 临时同步桥接 | Kernel 和 Compactor 都用 asyncio 桥接包装 async 调用 | |
| Phase 13 顺便改 Compactor | Compactor._summarize() 只有一行改动，顺带改掉。Kernel 用临时桥接 | ✓ |
| You decide | Claude 决定 | |

**User's choice:** Phase 13 顺便改 Compactor
**Notes:** ContextCompactor._summarize() 改动量极小（一行 await），顺带处理减少 Phase 17 工作量。AgentKernel._call_llm() 更复杂，留给 Phase 17 正式改造。

---

## llm_factory 返回类型

| Option | Description | Selected |
|--------|-------------|----------|
| 保持同步返回 | build_provider() 继续同步返回未初始化的 provider，调用者 async with 管理生命周期 | ✓ |
| factory 变 async | build_provider() 改为 async def，内部 await __aenter__ 后返回 | |
| You decide | Claude 决定 | |

**User's choice:** 保持同步返回
**Notes:** Factory 职责保持单一：配置解析 + 对象构造。生命周期管理由调用者负责。

---

## 测试策略

| Option | Description | Selected |
|--------|-------------|----------|
| 只改 provider 单元测试 | 迁移 test_openai_provider.py + test_llm_factory.py，新增生命周期测试 | ✓ |
| 全部迁移 | 8 个测试文件全部迁移为 async | |
| You decide | Claude 决定 | |

**User's choice:** 只改 provider 单元测试
**Notes:** 6 个集成测试涉及 Kernel/Exp 调用链，等 Phase 17-18 随实现一起迁移更合理。

## Claude's Discretion

- httpx.AsyncClient 超时配置细节
- chat_stream async generator 实现方式
- 异常映射调整
- Kernel 临时同步桥接具体方式
- ContextCompactor 调用链 async 改造范围

## Deferred Ideas

None
