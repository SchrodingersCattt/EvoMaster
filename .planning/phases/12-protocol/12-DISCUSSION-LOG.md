# Phase 12: Protocol 层 + 测试基础设施 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-26
**Phase:** 12-Protocol 层 + 测试基础设施
**Areas discussed:** 过渡策略, 覆盖范围, retry 处理, 测试设施, Validation Helper, Tool 签名, 返回类型

---

## 过渡策略

| Option | Description | Selected |
|--------|-------------|----------|
| 只改签名，实现后续修 | Phase 12 只改 Protocol 定义和 BuiltinTool ABC 签名。现有实现保持 sync，在 Phase 13-18 逐步改为 async。测试用 async mock 验证新 Protocol。变更最小化，每个 phase 职责清晰 | ✓ |
| 改签名同时加 async 壳 | 改 Protocol 的同时给所有实现加 async def 壳（内部仍同步逻辑，不 await）。改完后整个代码库形式上一致。但一次性改动量大，且 async 壳内跑同步 I/O 会阻塞 event loop | |

**User's choice:** 只改签名，实现后续修
**Notes:** 无额外补充

---

## 覆盖范围

| Option | Description | Selected |
|--------|-------------|----------|
| 一并改 EventHandler + ReplyQueueLike | 除 REQUIREMENTS 的 4 个 Protocol 外，EventHandler（Phase 16 需要 async）和 ReplyQueueLike（Phase 15 需要 async）也改签名。WorkerRegistry 属 src/ 层不在 v2.0 范围 | ✓ |
| 严格按 REQUIREMENTS，只改 4 个 | 只改 LLMProvider, Tool, Hook, Guard。EventHandler 和 ReplyQueueLike 留到 Phase 15/16 各自处理 | |
| 全部 7 个都改 | 包括 WorkerRegistry 也改为 async。一步到位，但 WorkerRegistry 主要被 src/ 层使用，可能引入不必要的改动 | |

**User's choice:** 一并改 EventHandler + ReplyQueueLike
**Notes:** 无额外补充

---

## retry 处理

| Option | Description | Selected |
|--------|-------------|----------|
| 从 Protocol 移除，实现也删 | chat_with_retry 从 LLMProvider Protocol 和 OpenAIProvider 实现中都删除。重试逻辑已在 Kernel._call_llm() 中，Provider 级别 retry 是冗余的 | ✓ |
| 从 Protocol 移除，实现保留为内部方法 | Protocol 不再要求，但 OpenAIProvider 保留 chat_with_retry 作为内部便利方法供直接使用 | |

**User's choice:** 从 Protocol 移除，实现也删
**Notes:** 无额外补充

---

## 测试设施

| Option | Description | Selected |
|--------|-------------|----------|
| conftest + async mock factories | 创建 tests/conftest.py 提供 async mock factories（mock async LLMProvider, mock async Tool, mock async Hook），后续阶段直接复用。加上 async validation helper 的测试 | ✓ |
| 最小化 -- 只验证配置生效 | 写几个 async def test 确认 pytest-asyncio 正常工作即可，mock factories 留到各阶段按需创建 | |

**User's choice:** conftest + async mock factories
**Notes:** 无额外补充

---

## Validation Helper

| Option | Description | Selected |
|--------|-------------|----------|
| 实例级检查器 | validate_async_protocol(obj, protocol_cls) -- 给定实例和 Protocol 类，检查实例的每个 Protocol 方法是否是 async def。用于测试和 Exp.assemble() 组装时的早期检测 | ✓ |
| 仅测试用工具 | validation helper 只在 tests/ 中使用，不在运行时检查。简单够用，但不能在组装时捕获错误 | |
| 类装饰器 | @validate_async_protocol(LLMProvider) 加在实现类上，类定义时就检查。最早检测，但侵入性最强 | |

**User's choice:** 实例级检查器
**Notes:** 无额外补充

---

## Tool 签名

| Option | Description | Selected |
|--------|-------------|----------|
| 保持 execute 命名，两个都改 async | Tool Protocol.execute() 和 BuiltinTool.execute()/_execute() 全部改为 async def。不改名，REQUIREMENTS 中 PROT-02 的 run() 是笔误 | ✓ |
| 重命名为 run() | 借此机会把 Tool Protocol 的方法改名为 run()，与 REQUIREMENTS 对齐。但改动量更大，影响 12 个 BuiltinTool + ToolRegistry | |

**User's choice:** 保持 execute 命名，两个都改 async
**Notes:** 无额外补充

---

## 返回类型

| Option | Description | Selected |
|--------|-------------|----------|
| 是，返回类型一并改 | Protocol 签名包括返回类型。chat_stream 改为 async def 返回 AsyncIterator[StreamChunk]。这是合约定义的一部分 | ✓ |
| 暂不改，留到 Phase 13 | Phase 12 只改方法为 async def，返回类型保持 Iterator。Phase 13 实现时再改返回类型 | |

**User's choice:** 是，返回类型一并改
**Notes:** 无额外补充

---

## Claude's Discretion

- async mock factories 的具体实现细节（AsyncMock vs 手写 async def）
- validation helper 的错误消息格式
- conftest.py 中 fixture scope 选择
- Hook Protocol 各方法的返回类型是否需要调整

## Deferred Ideas

None -- discussion stayed within phase scope
