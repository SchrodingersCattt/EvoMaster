# Phase 18: Exp 生命周期异步化 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-29
**Phase:** 18-exp
**Areas discussed:** assemble/build_runtime async 策略, cleanup callback async 支持, service 层过渡桥接, spawn_fn async 链路细节

---

## assemble/build_runtime async 策略

| Option | Description | Selected |
|--------|-------------|----------|
| 全改 async（推荐） | 按 EXPL-01/02 全部改 async def。当前无真正 async I/O 不影响正确性，但统一接口，未来 MCP 网络初始化可直接 await。与 Protocol hard cut 决策一致。 | ✓ |
| 只改 run() | 仅 run() 改 async（唯一有真正 async 调用的）。assemble/build_runtime 保持 sync。避免无意义的协程开销，但未来 MCP async 初始化时还需再改。 | |
| build_runtime + run async | assemble() 保持 sync（纯数据转换永远不需要 I/O），build_runtime() 和 run() 改 async。折中方案。 | |

**User's choice:** 全改 async（推荐）
**Notes:** 无额外说明

---

## cleanup callback async 支持

| Option | Description | Selected |
|--------|-------------|----------|
| 支持 async（推荐） | _run_cleanup_callbacks 改 async def，内部检测 callback 是否是 coroutine，是则 await，否则直接调用。兼容现有 sync callback，也为未来 MCP async close 预留。 | ✓ |
| 保持 sync | 不改 cleanup 机制。当前所有 callback 都是 sync，如果未来需要 async cleanup 再单独处理。避免引入 iscoroutinefunction 检测的复杂度。 | |
| 全部改 async | 所有 cleanup callback 强制为 async callable。现有 sync callback 调用方在注册时包装。简化内部逻辑但需要改所有注册点。 | |

**User's choice:** 支持 async（推荐）
**Notes:** 无额外说明

---

## service 层过渡桥接

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 18 加桥接（推荐） | agent_run_service 中用已有的 bridge loop 一并 run_until_complete(exp.build_runtime(...))。与 Phase 17 在 Exp.run() 加 bridge 同理，保证 Phase 18 完成后全部测试仍然通过。Phase 19 再整体重构。 | ✓ |
| Phase 19 统一处理 | 不在 Phase 18 改 agent_run_service。但这意味着 Phase 18 完成后 service 层会 break，需要接受 Phase 18-19 之间 service 层不可用的窗口期。 | |
| Exp 提供 sync wrapper | Exp 类上加 build_runtime_sync() wrapper，内部创建 loop + run_until_complete。调用方零改动。但与 Protocol hard cut 决策矛盾，且增加 API 表面积。 | |

**User's choice:** Phase 18 加桥接（推荐）
**Notes:** 无额外说明

---

## spawn_fn async 链路细节

| Option | Description | Selected |
|--------|-------------|----------|
| 覆写 execute()（推荐） | SpawnTool 直接覆写 async execute()，跳过 _execute + to_thread，直接 await spawn_fn()。干净利落，spawn 本身就是 async 操作不需要线程。但略微破坏 BuiltinTool 的 _execute 模式。 | ✓ |
| BuiltinTool 加 _aexecute | 在 BuiltinTool 基类加可选的 async _aexecute()，execute() 优先检查 _aexecute，有则 await，无则走 to_thread(_execute)。保持基类模式统一，但增加复杂度。 | |
| spawn_fn 保持 sync 入口 | spawn_fn 签名不变，内部用 asyncio.run() 调用 async 链路。SpawnTool 无需改动。但产生嵌套 event loop，且与 Phase 18 目标（全链路 async）矛盾。 | |

**User's choice:** 覆写 execute()（推荐）
**Notes:** 无额外说明

---

## Claude's Discretion

- _init_*_tools 内部 helper 方法是否也改 async
- _resolve_compaction_llm 是否改 async
- 测试迁移策略和范围
- SpawnTool 覆写 execute() 后 _execute() 的处理

## Deferred Ideas

None -- discussion stayed within phase scope
