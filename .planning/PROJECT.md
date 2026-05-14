# MatMaster — Agent Runtime & Context Platform

## What This Is

MatMaster 是 deepmodeling 团队的 agent 运行时与 API 平台，由 `matmaster/` 核心包（agent 内核、tool 运行时、skills、provider、MCP 接入、sessions）与 `src/` 平台层（FastAPI API、DAO、会话编排、Worker）组成。它把 Bohrium 远程执行资源、技能化 MCP 工具、playground 工作区和 LLM 推理串成一条生产链路：`API → playground.py → exp.py → AgentKernel → RunEventFanout`。当前 v3.0 milestone 聚焦于上下文系统的统一重构。

## Core Value

让"模型可见 user context"的装配只发生在 `matmaster/context/` 内核模块；前端回放、后端续跑、压缩恢复、prompt cache 四个用例从同一份事件流可恢复；AGENT.md 改动下一轮立即生效。

## Current Milestone: v3.0 Context System Unification

**Goal:**
1. 把 matmaster 项目里所有"模型可见 user context"装配路径统一到 `matmaster/context/` 内核模块的 `ContextAssembler`，消除散落在 6 处（`core/context_builder.py`、`core/context_compactor.py`、`manifests/`、`types/current_input.py`、`types/context.py`、`core/agent.py:337-347`）的旧装配逻辑。
2. 通过 Port Protocol 反转依赖：核心模块只声明所需平台能力（events 查询、AGENT.md 读取、jobs 查询），由 `src/services/` 实现 Port 作为回调注入；注入完成后核心模块不再依赖任何 service 模块。
3. 服务层不得直接装配上下文（不能调 `UserTurnContext.from_sources`、不能组合 `Source(...)`、不能拼装 sections），只能调 `ContextAssembler.assemble_turn` / `assemble_compaction`。
4. 让前端回放、后端续跑、压缩恢复、prompt cache 四个用例从同一份事件流可恢复，AGENT.md 改动下一轮立即生效。

**Target features:**
- **Phase 1 — 前置改造**：DAO 改造（`add_event` 返回 inserted id）+ 大文件拆分（`agent.py` / `agent_run_service.py` / `stream_service.py` 各 < 800 行）
- **Phase 2 — 事件语义**：`user_turn_context` 事件 + `history_checkpoint` v1 payload + AGENT.md hash anchor + ModelHistoryRestore v0/v1 分流
- **Phase 3 — Context 内核 + 装配三件套**：`matmaster/context/` 全量新模块 + Ports/Recipes/Assembler；service 层全切到 `assemble_turn` 入口
- **Phase 4 — Compaction 接入 + Prompt 形态**：`compaction.py` 接 assembler；checkpoint 切 v1 marker；fallback 命中率埋点；`<turn_attachments>` A/B 决策
- **Phase 5 — 清理 + 退役**：删除全部 shim；字段 rename；`COMPAT:` 标记退役

详见 `.planning/REQUIREMENTS.md` 与 `.planning/ROADMAP.md`。

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(本次 GSD 框架以 reset 模式启动；历史 v2.1 / v2.2 milestone 验证结果保存在 `.planning/milestones/`，未回填至此。Validated 将随 v3.0 phase 推进逐条追加。)

### Active

<!-- Current scope. Building toward these. -->

详见 `.planning/REQUIREMENTS.md` 的 v3.0 需求清单。摘要：

- [ ] **DAO**: events DAO/service 全链路返回 inserted event id
- [ ] **SPLIT**: 3 个 > 900 行的关键文件拆分至 < 800 行
- [ ] **EVT**: `user_turn_context` 事件类型注册 + payload schema v1 + SSE 隐藏 + `history_checkpoint` v1 扩展
- [ ] **HASH**: AGENT.md hash-triggered anchor 决策（首轮 / hash 不变 / hash 变化 / 50KB 截断 / 文件不存在）
- [ ] **RESTORE**: ModelHistoryRestoreService schema-aware 分流（v0 委托 `ChatHistoryConverter`，v1 消费 `user_turn_context` + `assistant_state` + `response` + `tool_result`）
- [ ] **CTX**: 新建 `matmaster/context/` 内核（sections / turn_context / rendering / session / system_prompt / history_restore / scanner + 9 个 source 模块）
- [ ] **ASM**: 装配三件套 — `ports.py` + `recipes.py` + `assembly.py` + `turn_intent.py` + 平台 Port 实现 + `AgentRuntimeSpec` 注入 + service 入口切换
- [ ] **SHIM**: 旧 `manifests/` / `types/context.py` / `types/current_input.py` 退化为 shim；删除 `_apply_user_instructions_to_initial_user_query`
- [ ] **CMP**: `compaction.py` 迁移 + assembler 接入 + checkpoint v1 marker + fallback 埋点
- [ ] **PROMPT**: `<turn_attachments>` 拆分 flag + offline eval + A/B 决策
- [ ] **CLEAN**: 删除全部 shim + `context_builder` → `system_prompt_builder` rename + 测试目录迁移 + `COMPAT:` 退役

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

按 DESIGN.md §18 明确不在本次范围：

- **Bohrium job table + hot cache 系统** — 仅占位 `SessionJobsSource`；数据接入留待 bohrium tool job table 与 hot cache 建好后单独接入，本次只锁定 `SessionJobsPort` 接口契约。
- **Oversized input offload (Case 3 / DESIGN Phase 4)** — 需独立 spec 设计 `InputSummaryConfig`、原文写盘策略、路径安全、失败处理；本次只在 `user_turn_context.transform="oversized_summary"` 与 `ContextCompactor.apply_compaction_plan(summary_override, session_attachments_override)` 预留字段。
- **`run_meta` typed 化** — `run_meta` god bag 整改不在本次范围；未来单独重构。
- **LLM provider 抽象层重构** — 当前走 `matmaster/providers/openai_provider.py` 不变。
- **Tool calling schema 重写** — 不动当前 tool schema。
- **前端 chat 历史展示组件改造** — 仍走现有 SSE replay 与 `ChatHistoryConverter`。
- **AGENT.md `/reload-agent-md` 显式命令** — 本次只做 hash-triggered anchor，不做显式 reload 命令。
- **Kernel `assistant_state` 写入条件扩展** — 自然结束写 `assistant_state` 的语义保持不变。
- **Sub-agent checkpoint 语义扩展** — `spawn_id` 仅保持现有 root/child 过滤语义；不扩展 child checkpoint。
- **Fallback 路径删除** — `sliding_window` / `tool_truncation` 保留并加埋点；删除决策依赖 Phase 4 埋点的命中率数据，独立 PR。
- **v2.x milestone 历史回填** — `.planning/milestones/v2.1-MILESTONE-AUDIT.md` 与 `v2.2-MILESTONE-AUDIT.md` 已记录历史结果，本次按 GSD `--reset` 启动不回填到顶层 `MILESTONES.md`。

## Context

**项目结构（当前真实）：**
- `matmaster/` 核心包 — agent 内核（`core/`）、tool 运行时（`tools/`）、技能（`skills/`）、LLM provider（`providers/`）、MCP 接入（`mcp/`）、sessions
- `src/` 平台层 — FastAPI API、DAO、会话与运行编排、Worker
- `config/` — `config.yaml` / `llm_config.yaml` / `mcp.yaml` / `mcp_config*.json`
- `tests/` / `evaluation/` / `docs/`

**生产主链路：**
`src/services/agent_run_service.py` → `matmaster/core/playground.py` → `matmaster/core/exp.py` → `matmaster/core/agent.py` (AgentKernel) → `matmaster/integration/fanout.py`

**技术栈：**
- Python `>=3.10`，依赖管理用 `uv`
- FastAPI Web 服务，Redis 协调
- Bohrium 远程执行与计算资源
- LLM 通过 `matmaster/providers/openai_provider.py` 走 OpenAI 兼容客户端（默认 LiteLLM Proxy）

**历史 milestone（保留在 `.planning/milestones/`，未回填）：**
- v2.1 (phases 25-30) — matmaster/ 完全独立化（不再 import `evomaster/` / `playground/` / `src/`）
- v2.2 (phases 32-36) — AgentKernel Generator-First + Tool Runtime v2 全链路 + Bus 移除

**设计稿：**
- `.planning/context-refactor/DESIGN.md`（v3.1，2554 行）— Context 模块统一重构设计稿，已三轮修订（v2 → v3 → v3.1，v3.1 引入 Ports + Recipes + Assembler 三层架构）
- `.planning/codebase/`（ARCHITECTURE / STACK / STRUCTURE / CONVENTIONS / TESTING / INTEGRATIONS / CONCERNS）— 现状分析锚点

**当前迁移残留（识别为非现状代码入口）：**
- 文档与注释中仍有 `playground/mat_master/`、`run.py`、`evomaster/` 旧路径引用
- 优先以 `matmaster/`、`src/`、`config/` 与对应测试为准

## Constraints

- **装配分层（DESIGN §4.2，12 条不变量）**：
  - 生产代码不得手写 `UserTurnContext.from_sources(...)`；唯一例外是 `ContextRecipe.apply()`
  - 生产代码不得组合 `Source(...)` 实例；唯一例外是 `recipes.py` 内的 step 函数与 source 单测
  - `agent_run_service` 不知 anchor/continuation 包含哪些 source；`ContextCompactor` 不知 compacted context 包含哪些 source
  - `ContextAssembler` 不判 intent、不读 hash、不处理迁移兼容、不写事件
  - 同一 turn 内 `UserInstructions` 必须原样传给 assembler，禁止二次读取 AGENT.md
  - Port 返回 typed snapshot/bundle/event sequence；不返回 service 对象、`ContextSection`、`UserMessage`、`UserTurnContext`；不使用 `Any` / `dict[str, Any]`；不返回已装配 sections
  - Event payload 用受限 `JsonObject` 类型别名表达（不含 `Any`）
  - `SessionContextBuilder` 属于 `matmaster/context/session.py`；Port 实现层不得返回已装配 sections
- **事件不变量（DESIGN §4.1）**：
  - 每个 `source_query_event_id` 在 events 表中对应**最多一条** `user_turn_context`
  - `user_turn_context` 写入失败时本轮 fail-fast；`history_checkpoint` 写入失败时本轮可继续（compaction 路径记 `failure_reason`）
  - 前端 SSE 回放与实时流永不发 `user_turn_context` / `assistant_state` / `history_checkpoint`
  - `invocation_id` = 一次用户请求；`spawn_id` 只保持现有 root/child 过滤语义
- **AGENT.md 处理**：50KB size cap，超限走 truncate + warning（不 fail-fast，保持 UX）
- **Schema 演化**：`schema_version` 决定 payload codec，`render_version` 决定 message content 解释；restore 按 schema 分发，不重渲染历史 sections
- **Tag escape（DESIGN §6.4）**：`wrap_tag` 对用户可控内容做最小 escape，防止 `</tag>` 注入破坏 section 边界
- **向后兼容窗口**：Phase 2 标记 `COMPAT:v0-restore` / `COMPAT:v0-checkpoint-marker` / `COMPAT:legacy-runtime-injection-helper`；Phase 5 退役条件 = 活跃 session 已迁移或产品确认不再恢复旧 session（> 30 天观察窗口）
- **Phase 1 直接切新路径**：不引入 runtime 分流开关；新 turn 一律走 `user_turn_context` 写入；风险收敛到测试与兼容读取路径，写入失败率监控 < 0.1%

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 命名 v3.0（major 升级） | 事件 schema + restore 路径大改，DESIGN.md 自身已迭代到 v3.1 | — Pending |
| GSD `--reset` 启动，不回填 v2.x 历史 | 旧 milestone 已审计归档；回填工作收益低 | — Pending |
| 装配收口到单一 `ContextAssembler` 入口，service 只通过 Port 提供回调 | 杜绝 `agent_run_service` / `ContextCompactor` 直接拼装 source 的散漫现状 | — Pending |
| Ports + Recipes + Assembler 三层架构（v3.1 新增） | v3 末尾 §7.3 / §8.2 / §9.2 仍残留 source 装配决策；三层化彻底收口 | — Pending |
| `UserTurnContext.from_sources` 降级为底层机械合并器，仅 `ContextRecipe.apply()` 调用 | 装配规则集中到 recipes，不再分散 | — Pending |
| AGENT.md hash-triggered anchor（不做显式 `/reload`） | hash 变化下一轮即生效，无需用户主动触发 | — Pending |
| `history_checkpoint` v0/v1 双 marker 兼容期 | Phase 2 双 marker 接受、写 v0；Phase 4 写 v1；Phase 5 退役 v0 | — Pending |
| `ModelHistoryRestoreService` schema-aware 分流（v0 委托 legacy，v1 走新算法） | 平滑迁移，旧 session 不中断 | — Pending |
| Prompt 形态 `<turn_attachments>` 拆分延后到 Phase 4 offline eval | 与现状等价 vs 拆分形态需 A/B 验证，避免盲改 | — Pending |
| Fallback (`sliding_window` / `tool_truncation`) Phase 4 埋点保留 | 删除决策依赖命中率数据，无数据先不删 | — Pending |
| `SessionJobsPort` Optional | bohrium job table 系统未建好，Optional 表示能力不可用，assembler 不感知具体产品功能 | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-14 after milestone v3.0 initialization*
