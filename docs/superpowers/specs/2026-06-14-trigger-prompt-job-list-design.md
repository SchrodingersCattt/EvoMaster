# Trigger 唤醒 Prompt 设计：prompt 自带 job 清单取代 delivery section

日期：2026-06-14
状态：已定稿，可实施。前置依赖 workspace job context section spec 已落地（merge 5ccab4bb：mode 分流 / snapshot 收紧 / 命名迁移 / 双 port），接入点已对齐真实代码（落地核对见 §1.2）。本 spec 设计主体（render_prompt 瘦身 / WorkspaceJobs mode 字段 / delivery 处理指令 / overflow 涵盖句）尚未实现，待按 §7 改动点清单落地。

## 1. 背景

monitor 服务在 Bohrium 作业到达终态后，通过 `trigger_run` 程序化唤醒一次 agent run。当前这条链路的 prompt 是设计期占位，没有详细设计：

- `src/services/bohrium_completion_scheduler.py` 的 `decide()` 判定唤醒原因（`FINAL` / `FIRST_FAILURE` / `PROGRESS` / `STALLED`），`render_prompt(reason, counts, first_failed)` 渲染一段概要文案，尾部拼 `_DELIVERY_SCOPE_SUFFIX`。
- 概要文案进入 `job['user_prompt']`，走和用户消息相同的组装链路，最终成为 `<turn_instruction>` section。
- 真正的 job 详情由 context 组装侧自动注入的 `<session_jobs>` delivery section 承载（数据来自 worker 侧 delivery snapshot 的 `snapshot.rows`）。

也就是说现状是「概要 + 指向」模式：prompt 说「有作业完成了，详情见 context」，agent 顺着指向去 section 找数据。

### 1.1 决定一切的约束：worker 无条件 ack

worker 对所有成功 run **无条件** confirm（`src/worker/agent_worker.py:526`）：

```python
if run_success and delivery_snapshot is not None:
    bohrium_delivery_ack.confirm(delivery_snapshot)   # 只看 run 是否成功
```

`confirm` 把 `snapshot.rows ∪ observed_terminal` 全部标 `handled`（`src/services/bohrium_delivery_ack.py:96`）。`snapshot.rows` 是 run 起点查到的该 `session(+workspace)` 全量 pending terminal jobs。

把这条链接起来：

1. run 起点锁定一批 pending terminal jobs（`snapshot.rows`）。
2. run 只要成功，就无条件 ack 这一批——**不检查 agent 是否真的处理了它们**。
3. 被 ack 的 job 标 `handled`，`decide()` 永不再触发它。
4. **所以：若 prompt 没能驱动 agent 真处理这批 job，agent 随便回一句也算 run 成功 → 这批 job 被盲 ack、交付永久丢失。**

`bohrium_delivery_ack.py` 开头那句「ack 范围 = agent 看到范围」是设计想要的等式，但它不会自动成立，必须靠 prompt 强行保证。因此在这条链路里，**prompt 的真实身份不是提示，而是「将被 ack 的工单」**：它必须把会被 ack 的 job 摆给 agent，并强制它逐一处理。

### 1.2 与 workspace job context section spec 的关系

本设计是 `docs/superpowers/specs/2026-06-13-workspace-job-context-section-design.md` 明确列出的「后续 prompt 设计」（该 spec §3 非目标、§5.3）。它踩在那份 spec 的基座上，前置依赖其落地：

- `job_context_mode` 分流（worker 读 `origin`：`bohrium_completion` → `session_workspace_delivery`，否则 `workspace_observation`）。
- `session + workspace` 收紧的 delivery snapshot。
- `SessionJobs → WorkspaceJobs` 命名迁移、delivery / observation 两种 read port。

本 spec 不重复这些基座设计，只在 delivery mode 上做 prompt 重构。

落地核对（2026-06-14，对照 merge 5ccab4bb）：

- mode 分流链已通：worker 读 `origin` 设 `job_context_mode`（`src/worker/agent_worker.py:355`）→ `run_agent`（`src/services/agent_run_service.py:268`）→ `build_bohrium_jobs_ports`（`:549`）→ 构造 `_SessionWorkspaceDeliveryJobsPort` / `_WorkspaceObservationJobsPort`（`src/services/bohrium_jobs_wiring.py`）。
- delivery port 的 pending 即 `delivery_snapshot.rows`（`bohrium_jobs_wiring.py:173`）——核心不变量数据源对齐。
- `SectionOrder` 为 `TURN_INSTRUCTION=1000` / `WORKSPACE_JOBS=1200` / `TURN_INSTRUCTION_LAST=1300`（`matmaster/context/sections.py`），§5.1 顺序可直接落。
- `render_prompt` 未被触碰（仍 `(reason, counts, first_failed)` + `_DELIVERY_SCOPE_SUFFIX`），正是本 spec 要改的。
- 唯一需新增（非沿用）：mode 信息当前止于 wiring 层 port 构造，渲染层拿不到——见 §5.4。

## 2. 目标

让 `bohrium_completion` trigger run 的 prompt **自带完整 job 清单**，从而取代单独注入的 delivery section。

核心不变量：

```
待处理清单(pending terminal)  ==  snapshot.rows  ==  worker ack 范围
```

三者是同一批 job：agent 看到的待处理清单 == 它被要求处理的 == worker 将要 ack 的。这样「看到 = 处理 = ack」闭合，盲 ack 无处藏身。要让待处理清单 == `snapshot.rows`，**清单数据只能取自 worker 侧 `snapshot.rows`**——monitor 侧 `render_prompt` 时还没 snapshot，且 tick 时刻数据会和 run 起点漂移。

注：在跑的 active jobs 也一并展示（「还有这些在运行」的上下文），但它们尚无终态结果、不在 ack 范围、不要求处理；不变量只约束 pending terminal 这部分。

## 3. 非目标

- 不动 `workspace_observation`（用户主动 query）的 context：它的可见范围本就 ⊋ ack 范围，不能合并成「待处理清单」。本设计只重构 trigger / delivery mode。
- 不重设计 ack 边界、snapshot 范围、`job_context_mode` 分流——那些归 workspace job context section spec。
- 不让处理指令随 reason 分化成多套（见 §4.3）。
- 不在运行时代码中做迁移、兜底或兼容（项目约定：迁移而非兼容，render_prompt 旧文案与 `_DELIVERY_SCOPE_SUFFIX` 直接删除替换）。

## 4. 方案选择

### 4.1 prompt 自带清单 vs 现状「概要 + 指向 section」

采纳自带清单。

现状靠 `_DELIVERY_SCOPE_SUFFIX` 让 agent「顺着指向去看 section」，但在无条件 ack 下，指向一旦被忽略就是盲 ack。自带清单把「将被 ack 的 job」直接摆进 prompt 正文，与处理指令紧邻，消除「这里说话、那里给数据、靠指向连接」的割裂，让核心不变量在文本层显式成立。

### 4.2 构造位置：A 双段拼接 vs B 全下沉 worker

采纳 A 双段拼接。

- monitor 侧 `render_prompt(reason)` 只产出「原因头」（why），随 `payload.user_prompt` 传。
- worker 侧 snapshot 之后，把 `snapshot.rows` 渲染成「清单 + 处理指令」（what + how）。

职责分工：**monitor 管 why（它 `decide()` 判定的），worker 管 what + how（它 snapshot 的）**。reason 是 monitor 的判定结果，把它翻译成原因头是 monitor 职责的自然延伸；B 方案把 reason 当枚举丢给 worker 再翻译，反而把判定语义泄漏到 worker。A 改动也更小：`render_prompt` 瘦身保留，不必新增 payload reason 字段。

### 4.3 处理指令：统一一句 vs 随 reason 分化

采纳统一一句。

```
请逐一拉取并核对以上已结束作业的结果：成功项汇总关键产出，失败项诊断原因，
给出整体结论与下一步。处理完成即视为交付确认。
```

它已强制「逐一处理」（挡住盲 ack），而「汇总还是诊断」由 agent 看每条的 `status` 自适应——原因头给了语境、清单给了 status，信息足够。统一指令的结构红利：**worker 侧不需要知道 reason，`payload.user_prompt`（原因头）就够，A 方案最干净，无需新增 reason 字段。**

## 5. 架构设计

### 5.1 数据流

```
[monitor 进程] ── 管 why ──
decide() → reason
render_prompt(reason) → 原因头              # 瘦身：仅定性，无数字/无 job/无指向
trigger_run(原因头, origin="bohrium_completion", workspace=…)
  → payload.user_prompt = 原因头             # 不新增 reason 字段

[worker 进程] ── 管 what + how ──
snapshot(session, workspace) → snapshot.rows   # = 待 ack 范围
run_agent(… job_context_mode=session_workspace_delivery …)
  context assembly (delivery)：
    原因头     (turn_instruction,    order 1000)   # 来自 payload.user_prompt
    job 清单    (workspace_jobs,      order 1200)   # pending terminal=snapshot.rows(待处理) + active(在跑·上下文)
    处理指令    (delivery_directive,  order 1300)   # 统一文案，对抗盲 ack
    → 一条连贯 user message
run 成功 → confirm → ack snapshot.rows         # == 清单，三相闭合
```

section 顺序由现有 `SectionOrder` 天然支撑：`TURN_INSTRUCTION = 1000` < `SESSION_JOBS/WORKSPACE_JOBS = 1200` < `TURN_INSTRUCTION_LAST = 1300`（`matmaster/context/sections.py`）。

### 5.2 monitor 侧：`render_prompt` 瘦身

签名从 `render_prompt(reason, counts, first_failed)` 瘦成 `render_prompt(reason)`。四句原因头，纯定性、无数字、无 job 详情、无「详情见 context」指向：

| reason | 触发态 | 原因头 |
| --- | --- | --- |
| `FINAL` | `active == 0` | 本会话的 Bohrium 作业批次已全部到达终态。 |
| `PROGRESS` | 部分终态、仍在跑 | 本会话又有 Bohrium 作业到达终态、结果待处理，仍有作业在运行。 |
| `FIRST_FAILURE` | 首个失败、仍在跑 | 本会话出现失败的 Bohrium 作业，仍有作业在运行。 |
| `STALLED` | 待处理 + 全部失联 | 本会话有 Bohrium 作业结果待处理，另有作业长时间无法查询状态。 |

连带删除：

- `_DELIVERY_SCOPE_SUFFIX` 常量整体删除（它要解决的 overflow 防漏，改由处理指令承载，见 §6）。
- `_process_session` 中 `counts` 聚合计算删除（原仅服务 `render_prompt`）。
- `get_first_pending_failed` 调用与 DAO 方法删除（原仅服务 `FIRST_FAILURE` 文案点名 job_id；清单已逐条带 status，不需点名）。
- 保留 `primary_reason`：`delivery=DeliverySpec(notify=primary_reason is Reason.FINAL)` 仍需要它。

净效果：monitor 侧净删代码，`render_prompt` 只依赖 reason 一个入参。

### 5.3 worker 侧：delivery mode 渲染清单 + 指令

delivery mode（`job_context_mode == "session_workspace_delivery"`）下，`WorkspaceJobsSource` 的 `to_sections()` 产出两个 section：

1. **job 清单**（order `WORKSPACE_JOBS = 1200`）：两组——pending terminal（`snapshot.rows`，待处理、= ack 范围）与 active（`query_session_active`，在跑、仅作上下文）。复用现有 JSON 行格式与 `detail_limit` / overflow 压缩机制（现有 `_render_group` 逻辑）。pending terminal 组引子明确「以下作业已结束、结果待处理」，让 delivery 语境下这些即待交付项；active 组标注「以下作业仍在运行」。
2. **处理指令**（order `TURN_INSTRUCTION_LAST = 1300`）：§4.3 的统一文案，针对 pending terminal 组（active 仍在跑、无结果可处理，不在指令范围）；有 overflow 时外加涵盖句（见 §6）。

`workspace_observation` mode（用户 query）下，`to_sections()` 维持现状：只出观察用的 jobs section，**不出处理指令 section**，且措辞不得把可见 jobs 描述为 ack 范围（与 workspace spec §7.3 一致）。

### 5.4 mode 区分的接入点

mode 由 worker 读 `origin` 得到 `job_context_mode`，经 `run_agent` → `build_bohrium_jobs_ports` 传入（`src/services/agent_run_service.py:268,549`）。但落地核对发现，**它只在 wiring 层 port 构造时用一次（决定查哪些数据），之后即丢失**：`WorkspaceJobs` 数据对象（`matmaster/context/ports.py:98`）无 mode 字段，两个 port 返回结构相同；`WorkspaceJobsSource.from_jobs()`（`matmaster/context/sources/workspace_jobs.py`）无条件渲染 active / pending_terminal / recent_terminal 三组，无法区分 delivery 还是 observation。

故本设计要**新增**这条传递（与 workspace spec §7.2 给 `WorkspaceJobs` 加 `workspace` 字段同构，不新增 composition / 不改 `SectionOrder`）：

- `WorkspaceJobs` 加 mode 标记字段（delivery / observation）。
- 两个 port（`_SessionWorkspaceDeliveryJobsPort` / `_WorkspaceObservationJobsPort`）构造 `WorkspaceJobs` 时各自填 mode。
- `WorkspaceJobsSource.from_jobs()` 据 mode 分流：delivery 出「清单 + 处理指令」、observation 维持现状（delivery port 本就不填 recent_terminal，故不渲染该组）。

## 6. 关键设计点：overflow 与盲 ack

`detail_limit` 压缩在「自带清单 + 全量 ack」下会制造缺口：`snapshot.rows` 是全量（全部被 ack），但清单只逐条展示前 `detail_limit` 条，其余压成一行 overflow 摘要（`{count, by_status, job_ids:[…]}`）。被 ack 的 > agent 能逐条看到的——overflow 里的 job 若被忽略，仍是盲 ack。

处理：**不取消压缩**（极端情况几百个 job 会爆 prompt），而是让处理指令在有 overflow 时显式涵盖它们：

```
（末尾 overflow 摘要中的 job_ids 同属本批次，请按其 status 一并处理。）
```

仅在真发生 overflow 时追加这一句；无 overflow 时清单已逐条全列，不追加。这正是旧 `_DELIVERY_SCOPE_SUFFIX` 唯一有价值的内核（overflow 防漏），从「无差别后缀」改为「按需融入处理指令」。

盲 ack 缺口只在 pending terminal 组（它进 ack 范围）；active 组即便 overflow 也不涉及 ack，按上下文展示即可，无需涵盖句。

## 7. 改动点清单

| 组件 | 改动 | 净增减 |
| --- | --- | --- |
| `render_prompt`（`bohrium_completion_scheduler.py`） | 签名瘦成 `(reason)`；四句定性原因头；删 `_DELIVERY_SCOPE_SUFFIX` | 净删 |
| `_process_session`（同文件） | 删 `counts` 聚合、删 `get_first_pending_failed` 调用；保留 `primary_reason` | 净删 |
| `get_first_pending_failed`（`dao/bohrium_jobs_table.py`） | 删除（无其他调用者，落地时复核） | 净删 |
| `WorkspaceJobs`（`matmaster/context/ports.py`） | 加 mode 标记字段 | 小增 |
| 两个 read port（`bohrium_jobs_wiring.py`） | 构造 `WorkspaceJobs` 时各自填 mode 字段 | 小增 |
| `WorkspaceJobsSource`（`matmaster/context/sources/workspace_jobs.py`） | delivery：出清单(1200) + 处理指令(1300)；observation：维持现状、不出指令 | 小增 |
| `workspace_observation`（用户 query） | 不动 | 0 |

> `get_first_pending_failed` 的时序说明：workspace job context section spec §6.3(3) 刚给它补了 `workspace` 参数（为保留 FIRST_FAILURE 点名 job_id 的正确性）。本 spec 因 FIRST_FAILURE 原因头不再点名 job_id，使其失去唯一调用者而删除。这是顺序依赖下的预期演进——前一份 spec 在「保留点名」前提下增强它，本 spec 改变前提后移除它，非冲突。

## 8. 测试计划

- delivery mode 渲染：trigger run 的 user message 含原因头 + 清单 + 处理指令三段，顺序为 1000 / 1200 / 1300。
- 核心不变量：pending terminal 组展示的 job（含 overflow 行 job_ids）集合 == `snapshot.rows` == confirm 标 handled 的集合（不存在「ack 了 agent 没看到的行」）；active 组不计入 ack。
- overflow：`snapshot.rows` 超过 `detail_limit` 时，处理指令追加 overflow 涵盖句；不超过时不追加。
- 四句原因头按 reason 正确选取，且不含数字 / 不点名 job_id。
- observation mode 不受影响：仍出观察 section、不出处理指令、措辞不暗示 ack。
- `render_prompt(reason)` 不再依赖 counts / first_failed。

## 9. 验收标准

1. `bohrium_completion` trigger run 的 user message 自带完整 job 清单，无独立 `<session_jobs>` / `<workspace_jobs>` 指向式 section 与正文割裂。
2. 待处理（pending terminal）清单 == `snapshot.rows` == ack 范围（含 overflow 行 job_ids）；active 仅作上下文、不计入 ack。
3. 处理指令强制逐一处理，agent 按 status 自适应汇总 / 诊断。
4. monitor 侧 `render_prompt` 仅依赖 reason；`counts` / `first_failed` / `_DELIVERY_SCOPE_SUFFIX` 已删。
5. 用户主动 query 的 observation context 行为不变。
6. 净代码量在 monitor 侧下降。

## 10. 实现风险

- **盲 ack（须守住）**：worker 无条件 confirm。清单必须覆盖 `snapshot.rows` 全量（含 overflow），处理指令必须强制处理，否则仍会盲 ack。实现时不可让清单与 ack 范围脱节。
- **前置依赖（已落地）**：本设计引用的 `job_context_mode` / `session+workspace` snapshot / `WorkspaceJobs` 来自 workspace job context section spec，已随 merge 5ccab4bb 落地，接入点已核对（§1.2）。
- **mode 可达渲染层（须新增）**：已核对——`job_context_mode` 当前止于 wiring 层 port 构造，`WorkspaceJobs` 无 mode 字段、`WorkspaceJobsSource` 无法区分（§5.4）。本设计须新增 mode 字段并由两 port 填充，否则 delivery 的清单 + 指令与 observation 无从分流。
- **observation 不被波及**：处理指令仅限 delivery；务必不让它泄漏进 observation，避免把跨 session 可见 jobs 误描述为待 ack。
