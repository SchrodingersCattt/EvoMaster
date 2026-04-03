# Phase 34: Exp/Service 接入 + Hook 退役 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-02
**Phase:** 34-exp-service-hook
**Areas discussed:** ESIN-08 归属, 灰度开关策略, Wave B1/B2 拆分粒度

---

## Initial Gray Area Selection

User chose "Other" and referenced the advancement plan document:
`docs/plans/2026-04-02-v2.2-phase2-advancement.md`

All four original gray areas (Hook 退役策略, _stream_llm_items() 改造, run_agent_stream() 架构, Plan 拆分策略) were pre-answered by the advancement plan. Discussion focused on three areas where the advancement plan needed updating based on Phase 33 completion.

---

## ESIN-08 归属

| Option | Description | Selected |
|--------|-------------|----------|
| 推迟到 Phase 35 (推荐) | 跟 CMIG-05 一起处理，减少 Phase 34 变更范围 | |
| 放 Phase 34 | Service 切流时一并处理 | |
| 丢弃需求 | 不再考虑 | ✓ |

**User's choice:** 丢弃这个需求，不再考虑
**Notes:** User explicitly chose to drop ESIN-08 entirely, not defer it.

---

## 灰度开关策略

| Option | Description | Selected |
|--------|-------------|----------|
| 保留灰度开关 (推荐) | meta["tool_runtime_v2_enabled"] 灰度注入，B2 后删除 | |
| Wave B1 直接默认启用 | KGEN-06 已完成，不再依赖 Hook 路径，减少中间态 | ✓ |
| 由 Claude 判断 | 根据实际实现情况决定 | |

**User's choice:** Wave B1 直接默认启用
**Notes:** User chose the more aggressive approach. Rationale: KGEN-06 already completed in Phase 33, _run_items() already yields ToolCallEvent/ToolResultEvent natively, so the original reason for the gray-scale switch (Hook dependency) no longer applies.

---

## Wave B1/B2 拆分粒度

| Option | Description | Selected |
|--------|-------------|----------|
| 保持 B1+B2 两个 plan | 原始粒度，每个 plan 大但完整 | |
| 拆为 3 个 plan | B1 拆为内核改造+Service切流，B2 保持一个 | ✓ |
| 拆为 4 个 plan | 最细粒度，每个 plan 改动面小 | |

**User's choice:** 拆为 3 个 plan
**Notes:** Plan 1: _stream_llm_items() + Exp.run_stream() + skill overlay + Exp.build_runtime() 注入. Plan 2: AgentRunService.run_agent_stream() + ToolResult 契约 + source 归一化. Plan 3: Hook 退役全部.

---

## Claude's Discretion

- _stream_llm_items() 内部 yield 点的精确位置
- run_agent_stream() 中 bus.emit 与 generator 消费的桥接实现
- Hook 退役过程中测试文件组织方式

## Deferred Ideas

- ESIN-08 丢弃（用户明确决定）
- ConfirmationHook 迁移 — FUTR-02
- 去总线化 — Phase 36
