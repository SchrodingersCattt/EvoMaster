# Phase 11: SubAgent Spawn 机制 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 11-subagent-spawn
**Areas discussed:** SubAgent Tool 参数设计, 子 agent 事件路由模式, 子 exp 配置策略, 递归保护实现方式

---

## SubAgent Tool 参数设计

| Option | Description | Selected |
|--------|-------------|----------|
| 固定子 exp + task | execute({"task": "..."}) -- spawn_fn 闭包在注册时捕获 exp 名称，LLM 只需描述任务 | |
| 动态 exp + task | execute({"exp_name": "explore", "task": "..."}) -- LLM 通过 exp_name 选择子 agent 类型 | ✓ |
| 动态 exp + task + context | execute({"exp_name": "...", "task": "...", "context": {...}}) -- 结构化传递父 agent 状态 | |

**User's choice:** 动态 exp + task (无 context)
**Notes:** 用户明确后续会定义多个 exp（explore, research 等），对齐 Claude Code 内置 subagent 类型，因此需要 LLM 动态选择 exp。context 参数不需要，父 agent 将上下文写入 task 文本即可。

---

## 子 agent 事件路由模式

| Option | Description | Selected |
|--------|-------------|----------|
| 共享父 bus, source 不区分 | 子 agent EventEmitterHook 直接用父 MessageBus，source 保持 MatMaster。前端无改动 | |
| 共享父 bus + source 前缀区分 | 子 agent source 用 MatMaster:explore 等前缀，前端可区分父/子渲染 | ✓ |
| 子 bus 桥接转发 | 子 agent 有独立 MessageBus，桥接层转发到父 bus。完全解耦但复杂 | |

**User's choice:** 共享父 bus + source 前缀区分
**Notes:** 选择中间方案，保留前端区分能力但避免桥接复杂度。

---

## 子 exp 配置策略

| Option | Description | Selected |
|--------|-------------|----------|
| 独立 TOML 文件 | 每个子 exp 一个 TOML（explore.toml, research.toml），独立定义 tools/prompt | ✓ |
| 运行时从父 exp 派生 | ExpConfig 新增 derive() 方法，子 agent 只 override 差异部分 | |

**User's choice:** 独立 TOML 文件
**Notes:** 与现有 load_exp_config(name) 加载链路兼容，零改动。每个子 exp 独立维护 tools 和 system prompt。

---

## 递归保护实现方式

| Option | Description | Selected |
|--------|-------------|----------|
| Schema 排除 + spawn_fn 双保险 | 子 exp TOML 不含 sub_agent（LLM 不可见）+ spawn_fn=None 运行时兜底 | ✓ |
| 仅 spawn_fn=None 守卫 | SubAgentTool 仍注册但 execute 时拦截 | |
| 仅 Schema 排除 | 子 exp TOML 不列入 sub_agent，无运行时兜底 | |

**User's choice:** Schema 排除 + spawn_fn 双保险
**Notes:** 两层防护：声明式（TOML 白名单）+ 运行时（spawn_fn=None 守卫）。

---

## Claude's Discretion

- spawn_fn 闭包签名和返回值
- SubAgentTool description/json_schema 精细化
- stop_event 级联传播具体实现
- normalize_event_source 前缀解析规则
- chat_history.py source 判断兼容改法
- 子 exp TOML 具体配置值
- Phase 11 交付哪些子 exp TOML

## Deferred Ideas

None -- discussion stayed within phase scope
