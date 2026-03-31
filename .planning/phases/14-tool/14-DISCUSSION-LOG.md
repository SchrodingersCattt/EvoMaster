# Phase 14: Tool 系统异步化 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 14-tool
**Areas discussed:** BashTool 执行模式, to_thread 包装粒度, 非 BuiltinTool 适配范围, SpawnTool 过渡策略

---

## BashTool 执行模式

| Option | Description | Selected |
|--------|-------------|----------|
| 只做 to_thread 包装 | 当前无 session-free 场景，不引入新能力。session.exec_bash() 用 asyncio.to_thread 包装 | ✓ |
| 引入双模式 | 添加 session is None 分支：无 session 时用 asyncio.create_subprocess_exec，有 session 时用 to_thread | |
| 延后到 v2.1 | session-free 模式属于新能力，记录为 deferred idea | |

**User's choice:** 只做 to_thread 包装
**Notes:** 当前 BashTool 只有 session-dependent 模式，不存在 session-free 使用场景

---

## to_thread 包装粒度

| Option | Description | Selected |
|--------|-------------|----------|
| _execute 整体包装 | BuiltinTool.execute() 中 await asyncio.to_thread(self._execute, arguments)，子类零改动 | ✓ |
| 逐个 session 调用点包装 | _execute 改 async def，每个 session 调用用 await asyncio.to_thread() 包装 | |
| 混合策略 | 纯计算 tool 逐调用点包装，session-heavy tools 整体包装 | |

**User's choice:** _execute 整体包装
**Notes:** ABC _execute 需从 async def 回退为 sync def（Phase 12 改的签名不适用于 to_thread 策略）

---

## 非 BuiltinTool 适配范围

| Option | Description | Selected |
|--------|-------------|----------|
| 一并改造 | LazyMCPTool 和 SkillTool 在 Phase 14 一并改 async def execute | ✓ |
| 只改 BuiltinTool | ToolRegistry 用双路径判断（inspect.iscoroutinefunction），非 BuiltinTool 延后 | |

**User's choice:** 一并改造
**Notes:** ToolRegistry.execute() 改 async 后必须统一 await 所有 tool.execute()

---

## SpawnTool 过渡策略

| Option | Description | Selected |
|--------|-------------|----------|
| spawn_fn 保持 sync | 不改 spawn_fn 类型，通过 to_thread 包装 _execute 整体在线程池运行 | ✓ |
| spawn_fn 改 async + 桥接 | spawn_fn 类型改 async Callable，Exp 层提供外壳为 async 但内部仍 sync 的 callback | |
| TOOL-05 延后到 Phase 18 | spawn_fn async 化与 Exp async 化是同一件事 | |

**User's choice:** spawn_fn 保持 sync
**Notes:** TOOL-05 实质延后到 Phase 18 与 EXPL-04 一起处理

---

## Claude's Discretion

- LazyMCPTool / SkillTool 内部 to_thread 的具体包装方式
- ToolRegistry.execute() 的 normalize_tool_result 适配
- task tools 的 _execute 签名确认
- 测试迁移范围和 async mock 策略

## Deferred Ideas

- BashTool session-free 模式（asyncio.create_subprocess_exec）-- 未来 DevShell 场景
- TOOL-05 spawn_fn async callable -- 延后到 Phase 18
