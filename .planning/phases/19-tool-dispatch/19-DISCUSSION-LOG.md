# Phase 19: 服务层桥接 + 并行 Tool Dispatch - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-29
**Phase:** 19-tool-dispatch
**Areas discussed:** Bridge 统一策略, 并行 dispatch 安全策略, 并行 dispatch 错误处理, DevShell bridge

---

## Bridge 统一策略

| Option | Description | Selected |
|--------|-------------|----------|
| 统一单 loop（推荐） | 一个 daemon thread + run_forever，router/kernel/cleanup 全部通过 run_coroutine_threadsafe 提交。更干净，消除双 loop 架构 | ✓ |
| 保持双 loop 清理 | 保留当前两个 loop 架构，只做代码清理（移除临时注释、整理变量命名）。改动最小但两个 loop 之间无法共享状态 | |

**User's choice:** 统一单 loop
**Notes:** 无额外说明

---

## 并行 dispatch 安全策略

| Option | Description | Selected |
|--------|-------------|----------|
| 全部并行（推荐） | LLM 负责避免冲突的 tool_call 组合。与 Claude Code 等主流 Agent 做法一致。实现最简单 | ✓ |
| 分类并行 | 只读 tool（Read/Glob/Grep）并行，有副作用 tool（Bash/Write/Edit）保持串行。需要 tool 元数据标记 | |
| 可配置并行度 | 默认全部并行，通过 ExpConfig/TOML 配置 parallel_dispatch 控制。灵活但增加配置复杂度 | |

**User's choice:** 全部并行
**Notes:** 无额外说明

---

## 并行 dispatch 错误处理

| Option | Description | Selected |
|--------|-------------|----------|
| return_exceptions（推荐） | asyncio.gather(return_exceptions=True)，所有 tool 都执行完毕，失败的返回 exception 对象转为 ToolResult(error)。LLM 看到错误后自行重试 | ✓ |
| fail-fast 取消 | asyncio.gather(return_exceptions=False)，第一个失败就取消其余 tool。省资源但 LLM 只能看到一个错误，其余 tool 结果丢失 | |

**User's choice:** return_exceptions
**Notes:** 无额外说明

---

## DevShell bridge

| Option | Description | Selected |
|--------|-------------|----------|
| 最小改动（推荐） | 把现有 new_event_loop + run_until_complete 换成 asyncio.run()。DevShell 是开发工具，不需要和 service 层统一模式 | ✓ |
| 统一模式 | DevShell 也用 daemon thread + run_forever + run_coroutine_threadsafe。与 service 层一致但对 REPL 过度设计 | |

**User's choice:** 最小改动
**Notes:** 无额外说明

---

## Claude's Discretion

- Guard + pre_hook 串行门控 vs tool execute 并行的拆分方式
- 并行 dispatch 后 ToolMessage 追加顺序
- 统一 loop 生命周期管理细节
- stop_event 检查点位置调整

## Deferred Ideas

None — discussion stayed within phase scope
