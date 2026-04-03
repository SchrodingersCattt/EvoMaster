# Phase 36: 去总线化 + 高级调度 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-03
**Phase:** 36-去总线化 + 高级调度
**Areas discussed:** 去总线化范围, async fanout 设计, SessionCaps 调度, run_agent() 旧路径

---

## 去总线化范围

### 删除范围

| Option | Description | Selected |
|--------|-------------|----------|
| 完全删除 (推荐) | MessageBus + EventRouter 物理删除。Service 层改为 async fanout 直连 handler。DevShell 路径独立改造。ConfirmationHook 改为 event_sink。 | ✓ |
| 保留 DevShell 路径 | Service 层删除 Bus/Router，但保留 MessageBus 给 DevShell 用。删除 EventRouter，保留 MessageBus 作为工具类。 | |
| 降级不删除 | Bus/Router 保留但标记 deprecated。新 fanout 为主路径，Bus 继续服务 ConfirmationHook + DevShell。 | |

**User's choice:** 完全删除
**Notes:** 无额外备注

### ConfirmationHook 处置

| Option | Description | Selected |
|--------|-------------|----------|
| event_sink 回调 (推荐) | 和 Phase 34 对 ContextCompactor 的处理一致：接受 event_sink 参数而非 bus 引用。 | |
| 将 ConfirmationHook 迁入 ToolRunner | 集成到 FullToolRunner 执行链中，但涉及 Kernel 双向流复杂度高。 | |
| Claude 决定 | 根据实际实现复杂度选择。 | |

**User's choice:** 移除 ConfirmationHook（用户自定义输入）
**Notes:** 用户选择直接删除 ConfirmationHook 而非改造

### ConfirmationHook 移除确认

| Option | Description | Selected |
|--------|-------------|----------|
| 直接删除，后续重建 (推荐) | 当前确认功能前端未使用，先删除 ConfirmationHook + BaseHook + Hook Protocol 整套。待 generator 双向流设计稳定后在 v2.3+ 重新实现。 | ✓ |
| event_sink 过渡 | 先改为 event_sink 保留功能，跟 ContextCompactor 的处理方式一致。等 v2.3 再改为 generator 双向流。 | |

**User's choice:** 直接删除，后续重建
**Notes:** 无额外备注

---

## async fanout 设计

### 实现形式

| Option | Description | Selected |
|--------|-------------|----------|
| 简单函数 (推荐) | async def fanout() 纯函数，顺序调用 handler.handle()，SSEHandler 排第一保证低延迟，每个 handler 独立 try/except。 | |
| 并发 gather | asyncio.gather 并发执行。SSE 和持久化同时跑。但并发误写风险稍高。 | |
| SSE优先+持久化异步 | SSE 同步 await，持久化 fire-and-forget（create_task）。持久化完全不阻塞事件流。 | ✓ |

**User's choice:** SSE优先+持久化异步
**Notes:** 无额外备注

### 持久化 task 生命周期

| Option | Description | Selected |
|--------|-------------|----------|
| TaskGroup 收集 (推荐) | fanout 内维护 TaskGroup 或 set[Task]。persistence task 创建后加入集合，完成后自动移除。run 结束时 drain 剩余 task。 | ✓ |
| 无管理，纯 fire-and-forget | 不追踪 task。简单但失败无感知，run 结束可能有未完成持久化。 | |
| Claude 决定 | 根据实际实现复杂度和 EventRouter.stop() 的现有 drain 模式决定。 | |

**User's choice:** TaskGroup 收集
**Notes:** 无额外备注

---

## SessionCaps 调度

### 调度范围

| Option | Description | Selected |
|--------|-------------|----------|
| persistent shell 并发 (推荐) | 只做最有价值的一项：persistent shell 时 SESSION_SHELL plane 工具从 exclusive 降级为 shared_read。 | |
| 全部 4 个方向 | persistent shell + web_fetch 上限 + spawn 限并发 + 通用 SessionCapabilities 映射。 | |
| Claude 决定 | 根据当前代码中实际消费 SessionCapabilities 的场景决定。 | |

**User's choice:** 跳过 ASCH-01（用户自定义输入）
**Notes:** 用户指出当前 LocalSession 和 SSHSession 均为 shell_persistence="stateless"，无 persistent shell 实现，高级调度无实际消费场景

### 确认跳过

| Option | Description | Selected |
|--------|-------------|----------|
| 只做接口预留 (推荐) | ToolScheduler 增加 SessionCapabilities 感知接口，但当前全部 stateless，调度策略不变。 | |
| 跳过 ASCH-01 | 完全跳过，等有 persistent shell 实现时再追加。 | ✓ |
| 全面实现 | 即使是 stateless，仍实现完整的能力感知调度逻辑，通过 mock 验证。 | |

**User's choice:** 跳过 ASCH-01
**Notes:** 无额外备注

---

## run_agent() 旧路径

### 处置方式

| Option | Description | Selected |
|--------|-------------|----------|
| 同步改造 (推荐) | run_agent() 也删除 Bus/Router，改用 fanout。保持两条路径一致。 | |
| 删除 run_agent() | 旧接口直接删除，调用者迁移到 run_agent_stream()。最彻底。 | ✓ |
| 只改 stream，旧路径保留 Bus | 只对 run_agent_stream() ��总线化。run_agent() 继续走 Bus/Router。 | |

**User's choice:** 删除 run_agent()，改造完毕后将 run_agent_stream 更名为 run_agent()
**Notes:** 用户明确要求最终只保留一个执行��口

### Worker SSE 处置

| Option | Description | Selected |
|--------|-------------|----------|
| no-op send_cb (推荐) | Worker 调用时传入 no-op send_cb，SSEHandler 正常创建但 handle() 立即返回。或让 fanout 支持可选 handler。 | |
| Claude 决定 | 根据实际代码结构决定最干净的方式。 | ✓ |

**User's choice:** Claude 决定
**Notes:** 无额外备注

---

## Claude's Discretion

- Hook 基础设施清理范围（取决于 DevStreamHook 和 InlineToolRunner 的实际依赖）
- Worker 模式的 SSE 处理方式
- fanout 函数放置位置
- DevShell 路径改造方式
- InlineToolRunner 是否同步删除

## Deferred Ideas

- ASCH-01 高级调度 — 待 persistent shell 实现后追加
- ConfirmationHook generator 双向流重建 — v2.3+
