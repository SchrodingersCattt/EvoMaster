# Phase 8: BuiltinTool 基础设施与核心 Tools - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 08-builtintool-tools
**Areas discussed:** ToolContext 设计, BuiltinTool 基类层次, TaskTool 语义范围, Exp 注册切换

---

## ToolContext 设计

| Option | Description | Selected |
|--------|-------------|----------|
| 构造注入 | Tool Protocol 签名不变，session/workdir 在 Exp assemble 阶段通过构造函数注入。与 EvoToolAdapter 已验证模式一致，kernel 不感知 session。影响 2-3 文件。 | ✓ |
| execute 参数注入 | 修改 Tool Protocol execute() 签名增加 context 参数。工具无状态可复用，但破坏 @runtime_checkable Protocol，触发 4-5 文件联动变更。 | |

**User's choice:** 构造注入
**Notes:** 解除 STATE.md 标记的 ToolContext blocker。不引入 ToolContext 参数类型。

---

## BuiltinTool 基类层次

| Option | Description | Selected |
|--------|-------------|----------|
| 统一基类 | 一个 BuiltinTool 抽象基类，session/workdir 通过构造函数注入（可选）。消除 9 个 tool 的构造样板重复。新增 1 个文件。 | ✓ |
| 无基类 | 各 tool 直接实现 Tool Protocol。零抽象层，与项目 Protocol-first 风格一致，但样板分散。 | |

**User's choice:** 统一基类
**Notes:** Phase 8+9 共 9 个 tool，多数需要 workdir/session 注入，超过 3 个 tool 的共享阈值。

---

## TaskTool 语义范围

| Option | Description | Selected |
|--------|-------------|----------|
| 三分离 + workspace 文件 | TaskCreate/TaskUpdate/TaskGet 三个 tool + workdir/tasks.json 持久化 | |
| 单一 TaskTool + workspace 文件 | 1 个 tool，action 参数区分操作，注册成本低但 LLM 易混淆 | |
| 三分离 + 内存存储 | 三个 tool + 内存 dict，简单但 run 重启后丢失 | |
| 五分离 + workspace 文件 (user proposal) | 对齐 Claude Code: TaskCreate/TaskGet/TaskList/TaskUpdate/TaskComplete + workdir/.tasks.json | ✓ |

**User's choice:** 五分离 + workspace 文件持久化（用户提出对齐 Claude Code 的 5 tool 设计）
**Notes:** 用户认为 complete 与 update 是不同语义。workspace 持久化是硬需求，科研任务跨 run 生命周期。

---

## Exp 注册切换

| Option | Description | Selected |
|--------|-------------|----------|
| 保留 ["*"] wildcard 过渡 | Phase 8-9 保持 wildcard，_init_builtin_tools 内部双源注册（native + evo adapter），source 标签区分。Phase 9 后切显式列举。 | ✓ |
| 立即显式列举 | 现在改 TOML 为显式 tool 名列表，每次新增同步更新。可审计但频繁变动。 | |

**User's choice:** 保留 ["*"] 过渡
**Notes:** Phase 8 仅 3 个新 tool，Phase 9 还有 6 个，过渡期显式列举导致 TOML 频繁变动。

---

## Claude's Discretion

- BuiltinTool 基类具体字段设计
- tasks.json 文件格式和 schema
- BashTool/ListDirTool 通过 session 执行远程命令的具体机制
- _init_builtin_tools 内部代码组织

## Deferred Ideas

- ExpConfig.tools.builtin wildcard → 显式列举（Phase 9 后）
- 清除 EvoToolAdapter 依赖（Phase 9 后）
- MonitorJobTool 原生化（待评估）
