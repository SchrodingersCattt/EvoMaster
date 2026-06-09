# Bohrium 作业完成调度器（无状态闭环 v5）设计

> 状态：设计稿 v5（无状态调度 + worker 全量 delivery snapshot + session_jobs renderer 压缩）。
> 范围：精简闭环优先（lean），但显式覆盖几百~几千作业单批；调度单元为 invocation 级判定 + session 级合并；delivery snapshot 与 ack 落 worker；context 详情可压缩，全量 job_id 始终可见。
> 非目标：见 §11。上万级常态下的 id 分批 delivery、GPT 蓝图的 delivery_state 全量计数表、delivery_batch 三表、4 presets、失败阶梯阈值、heartbeat，均留待 Phase 2。

### v5 变更记录（对 v4 review 的修正）

- **落实 projection，升级 `session_jobs` renderer（决定改 kernel renderer）。** v4 §8 的 projection 规则在“不碰 kernel”前提下无法实现：`SessionJobsSource.from_jobs`（`matmaster/context/sources/session_jobs.py:21-33`）对每个 job **一对一全量 `json.dumps`** 渲染，没有摘要/详情分层。因预期 lean 期即出现几百~几千作业单批，v5 升级这个 renderer——其 JSON-line 格式本就标注为 intentionally temporary、非 product contract（`session_jobs.py:13-17`），改它名正言顺。升级后支持 `detail_limit`：前 N 个 job 渲染完整详情，其余压成一行溢出摘要（按 status 分组计数 + **全量 job_id 列表**）。`active` 与 `pending_terminal` 两段都做此压缩。
- **token 底线明确。** 第二级压缩压的是**详情**；**全量 job_id 是 token 底线**（O(单次 pending 数)），必须保留以守住“ack 范围 = agent 看到范围”。几百~几千可行；上万级常态下 id 列表本身成瓶颈，需 id 分批 delivery，仍留 Phase 2（§11）。
- **`detail_limit=None` 时 renderer 行为与 v4 前完全一致。** 普通用户轮次不传 detail_limit，渲染不变；只有 completion delivery 路径传有限值。
- **§12.4 与 §8 统一。** 口径定为：全量 job_id 必须 context 可见；详情可按 `detail_limit` 压缩；confirm 只 ack `snapshot.row_ids`。
- **identity 门保留为防御并标注语义。** 正常情况下一个 session 的 owner（user/org）稳定，identity 门不触发；它只防 owner 不一致的脏数据，不是常规路径。owner 不符的残留 pending 行属异常，Phase 2 GC。
- **上界粒度澄清。** §1/§12.3 的 `1+N` 是 per-invocation；session 级合并 trigger + session 级 snapshot/ack 下，K 个 invocation 的 session 总唤醒 ≈ K×(1+N)。“与作业数无关”成立，但非“与 invocation 数无关”。
- **snapshot 注入点写明。** 注入靠 completion **root run** 在 anchor turn 调一次 `load_session_jobs`（`matmaster/context/assembly.py:265-270`）生效；该 port 之后仅在 compaction 再被调，返回**同一** snapshot（本轮交付边界固定，符合预期）。

### v4 变更记录（仍有效）

- **delivery snapshot 是交付权威集合。** worker run 起点读 DB 时间 `before_ts`，查询该 session 在 `before_ts` 前全部 pending terminal rows，形成含完整 `row_id/job_id` 集合的 `DeliverySnapshot`；context 基于这个集合组装；confirm 只 ack snapshot 中的 row ids。`pending_terminal_job_N` 是 context projection，不是 delivery 边界。
- **去掉固定 `limit=5` 作为交付边界。** DB 层必须拿到本轮全部 pending terminal job id；详情可压缩，但不得静默截断后 ack 未暴露的 jobs。
- **progress 阈值向上取整。** `step = ceil(total / progress_segments)`，避免 `total // segments` 在小批量下把 progress 次数放大到超过 N。
- **NX 占位 key 用 pending row 高水位。** reservation key 用 session 内 eligible units 的 `max_pending_terminal_id`，避免秒级 `terminal_at` 碰撞导致新完成作业被旧占位压住一个 TTL。
- **identity/scope 收紧。** 调度扫描按 `user_id/org_id/session_id/invocation_key` 分组；触发前校验当前 session identity 与扫描行一致。`invocation` 只作 wakeup candidate，实际 delivery scope 是当前 `user/org/session` 的 snapshot。

### v3 变更记录（仍保留）

- **删掉全部持久调度态。** v2 为 regular 唤醒引入 `bohrium_delivery_state` 表（预算计数 + cooldown 基准）。v3+ 用**进度里程碑**替代：progress 在 `pending_terminal >= ceil(total/segments)` 时触发，唤醒次数上界 N 内生于阈值随 `total` 的缩放，**不需要任何计数器**。配合 worker ack 清零，“已发过几次”由 ledger 当前快照隐式表达。
- **三条原因全部无状态。** `decide` 只读 `scan_delivery_units` 的当前聚合快照，不读写状态表、不依赖 `now`。
- **连带删除**：`create_bohrium_delivery_state_table.sql`、`bohrium_delivery_state_table.py`、`record_regular_delivery` 写表分支、配置项 `regular_budget` / `regular_cooldown`。
- **唯一让掉的能力**：cooldown 的按时间响应。progress 按完成**数量**切段，慢批次中途反馈迟钝（pull 仍可查）。见 §11。

### v2 变更记录（仍有效）

- **P1-a（双发/多实例）**：`try_acquire_session_run`（sessions_service.py:461）只查本进程集合 + 无条件 `set_session_status(active)`（chat_sessions_table.py:244），跨进程失明；`mark_dedup_key_nx` 在入队后才执行且返回值被忽略（stream_service.py:428）。monitor 的 trigger_run 几乎从不返回 busy，单实例下也会在 run 进行中每 tick 重复入队。改为：触发前 **(1) 查 DB `get_session_status==idle`** + **(2) `mark_dedup_key_nx` 原子占位**。
- **P1-b（idle-before-ack 竞态）**：ack 必须在 `release_session_run` 之前完成，使 status 翻 idle 时 pending 已清零。
- **P2-a（snapshot 是交付权威集合）**：交付语义 **at-least-once**；snapshot cutoff 保证不漏。context projection 基于 snapshot 全量 job id 组装，confirm 只 ack snapshot row ids。
- **P3-a（scan_limit 落地）**：`scan_delivery_units(limit)` 显式入参 + `ORDER BY first_pending_terminal_at ASC ... LIMIT %s`（最老 pending 优先，防饥饿）。
- **P3-b（schema 口径）**：`bohrium_jobs` 主键是 `id`（AUTO_INCREMENT）；`(user_id, org_id, sandbox, job_id)` 是唯一键 `uk_owner_job_id`。

---

## 1. 目标

在已跑通的 Bohrium job ledger 与 poller 之上补齐 agent 唤醒闭环：某批作业进入终态时，按策略唤醒一次 agent run 去消费结果；run 成功后确认交付，避免同一批作业被无限重复呈现。

调度器只回答：**当前这些已终态、尚未交付给 agent 的作业，是否值得唤醒一次 agent run？** 它不 poll 平台，不分析结果，不持有任何跨 tick 状态。

成本硬约束：

```
poller 刷新 job 状态可以按 job 频繁做；
agent run 唤醒必须按批次、按里程碑、按背压做。
任一 invocation 的非 final 自动唤醒上界 = 1(first_failure) + N(progress)，与作业数无关。
（per-invocation 上界；K 个 invocation 的 session 总唤醒 ≈ K×(1+N)，与作业数仍无关。）
```

---

## 2. 背景：当前代码事实（已对 codex/provider-stage1 源码核实）

- **数据层完整。** `bohrium_jobs`（`src/sql/create_bohrium_jobs_table.sql`）一行一作业。主键 `id BIGINT AUTO_INCREMENT`；`(user_id, org_id, sandbox, job_id)` 是唯一键 `uk_owner_job_id`（create_bohrium_jobs_table.sql:4,34）。带 `session_id / invocation_id(nullable) / spawn_id(nullable) / status / terminal_at / handled_at / submitted_at`。CHECK 约束保证状态机：活跃态 `next_poll_at` 非空且 `terminal_at` 空；终态反之；`handled_at` 必先终态。索引含 `idx_session_pending(user_id, org_id, session_id, handled_at, terminal_at)`。
- **DAO 是唯一写入口**（`src/dao/bohrium_jobs_table.py`）。已有 `apply_poll / mark_handled / query_session_pending_terminal(limit=5) / claim_due_batch`。`mark_handled` 幂等但**生产代码从未调用**。v5 不复用 `limit=5` 作为 delivery 边界，新增窄字段全量 snapshot 查询。
- **Poller 链路已通。** `BohriumMonitor.tick()`（bohrium_poller.py:173）→ `run_once()` → claim → 查平台 → `apply_poll`。`monitor_worker.py` 每 10s 调 `tick()`。poller 只写 ledger。
- **触发接口现成。** `ChatStreamService.trigger_run(session_id, prompt, *, origin, dedup_key, delivery, on_busy, workspace, dedup_ttl_sec)`（stream_service.py:353）→ `TriggerResult(status ∈ enqueued|deduped|busy|error)`。`prompt` 写成一条 System `trigger` 事件作为本轮输入。`on_busy` 第一版等价 skip，scheduler 不当它可调互斥；真实互斥靠 status 门 + NX 占位。`get_stream_service()`（:895）模块级工厂。
- **触发的并发事实（P1-a 依据）。** `_prepare_run`（:269）→ `try_acquire_session_run`：仅本进程内存集合 + 无条件 DB UPDATE（恒 True），跨进程不互斥；`_enqueue_run`（:343）同步 `set_session_status('waiting')`。`get_session_status`（sessions_service.py:335）返回 `idle|active|waiting|failed` 且把过期 waiting reconcile 回 idle，**跨进程可见**。`mark_dedup_key_nx`（redis_dao.py:352）底层 `SET NX EX`，可作原子占位。
- **交付读侧已存在、且数据/渲染分层。** agent run 时 `agent_run_service.py:515-521` 调 `build_bohrium_jobs_ports(...)` 构造 `_RunSessionJobsPort`（`src/services/bohrium_jobs_wiring.py:144-211`，**service 层**，内部硬编码 `limit=5` 查 DB）。kernel 侧 `assembly.py:265-270` 的 `_load_jobs_or_empty` 在 **root run anchor turn 与 compaction** 时调 `port.load_session_jobs`，拿到 `SessionJobs`（`matmaster/context/ports.py`）后由 `SessionJobsSource.from_jobs`（`session_jobs.py:21-33`）渲染成 `pending_terminal_job_N`/`active_job_N`。**数据获取在 service 层、渲染在 kernel 层，两者分离**——这是 v5 注入 snapshot 不必动 kernel 决策逻辑、只升级 renderer 的基础。
- **Worker 收尾点。** `agent_worker.py` `finally` 内：先 `delete_interaction_run_active`，再 `if acquired: release_session_run(run_success=...)`（:513）→ 置 DB idle/failed（sessions_service.py:490）。

### 当前两个缺口

1. **触发缺口**：无任何东西在作业终态时唤醒 session。
2. **确认缺口**：`mark_handled` 从未被调用 → 终态作业永停 pending，每轮被 `limit=5` 渲染反复呈现最老 5 个。v5 通过 worker delivery snapshot + confirm 修复，确认范围以 snapshot 的全量 row ids 为准。

---

## 3. 架构：两条链路，ledger 状态驱动协调

两条链路各自落在已有进程内，**只通过 ledger 状态协调**（无任何额外调度态表），**不共享运行态**：

```
Monitor 进程（已有循环，每轮）
  ├─ poller.tick()      → 查平台、写 ledger（置 terminal_at）              [已实现]
  └─ scheduler.tick()   → 聚合扫描 → 逐 (session,invocation) 无状态判定 → 按 session 合并
                          → [identity 门 + status==idle 门 + NX 原子占位] → trigger_run [新增]
                          （判定纯 ledger 聚合，不读写任何持久态）

Worker 进程（已有 run 完成路径）
  ├─ run 起点：read_db_now() 取 before_ts；查询全量 pending terminal row ids
  │           → DeliverySnapshot(row_ids, job_ids, 轻字段 detail, counts)  [新增]
  │           → 传入 run_agent → port 据 snapshot 组装 SessionJobs（带 detail_limit）
  └─ run 成功收尾：先 confirm(snapshot) 按 snapshot.row_ids mark_handled，
                  再 release_session_run                                    [新增，顺序关键]
```

唤醒是 **attempt（monitor）**，交付确认是 **confirm（worker，RUN_END）**，两者分离。

**为什么 ack 必须在 worker、且在 release 之前：** context 渲染条件是 `terminal_at IS NOT NULL AND handled_at IS NULL`。monitor 若在发起时就 `mark_handled`，被唤醒的 run 渲染到空 pending，等于把要交付的东西标没了。`handled_at` 必须在 agent 成功消费本轮 delivery snapshot 之后才能置——只有 worker 知道 run 何时跑完。又因为跨进程 busy 门用 DB status，**confirm 必须在 `release_session_run` 置 idle 之前完成**：否则存在 release→tick→（monitor 见 idle+pending 重新触发）→ack 的竞态窗口（P1-b）。

**为什么无状态闭环自洽：** ack 同时是交付确认 + 进度里程碑的“翻篇”。run 成功后 ack 抹平 snapshot 中的 pending，`pending_terminal` 回落到阈值之下，下一轮 `decide` 不再对同一段触发；run 未成功则 pending 不动、下轮重试。因此 progress 不需要任何“已发过几次”的持久记录。

---

## 4. 数据模型：不新增任何表

**零新增表、零持久调度态。** 所有判定量从 `bohrium_jobs` 实时聚合。在 `BohriumJobsTable` 加四类窄方法：调度聚合、delivery snapshot、按 snapshot ack、DB 时间。

**(a) 聚合扫描** `scan_delivery_units(limit)`（P3-a：显式 limit + 确定序，最老 pending 优先）：

```sql
SELECT
    user_id,
    org_id,
    session_id,
    COALESCE(invocation_id, '')                                    AS invocation_key,
    MIN(workspace)                                                 AS workspace,
    COUNT(*)                                                       AS total,
    SUM(terminal_at IS NULL)                                       AS active,
    SUM(terminal_at IS NOT NULL AND handled_at IS NULL)            AS pending_terminal,
    SUM(status IN ('failed','stopped'))                           AS failed_total,
    SUM(status IN ('failed','stopped') AND handled_at IS NOT NULL) AS failed_handled,
    SUM(status = 'finished')                                       AS succeeded,
    MAX(terminal_at)                                               AS max_terminal_at,
    MAX(CASE WHEN terminal_at IS NOT NULL AND handled_at IS NULL
             THEN id END)                                          AS max_pending_terminal_id,
    MIN(CASE WHEN terminal_at IS NOT NULL AND handled_at IS NULL
             THEN terminal_at END)                                 AS first_pending_terminal_at
FROM bohrium_jobs
GROUP BY user_id, org_id, session_id, COALESCE(invocation_id, '')
HAVING pending_terminal > 0
ORDER BY first_pending_terminal_at ASC, user_id ASC, org_id ASC,
         session_id ASC, invocation_key ASC
LIMIT %s
```

扫描行显式带 `user_id/org_id`，scheduler 触发前用当前 session row 再校验 identity（§6a）。`total` 是该单元全部作业数，progress 阈值据此缩放。`max_pending_terminal_id` 仅用于 Redis NX 占位 key 高水位，不进业务语义。

> **identity 防御语义。** 正常情况下一个 session 的 owner 稳定，扫描行 `user_id/org_id` 即 session owner，identity 门恒过。若出现 owner 不符的残留 pending 行（脏数据/异常），identity 门会跳过它们；这类行不被消化、会占 `scan_limit` 名额，属异常而非常规路径，Phase 2 加 owner-aware GC。v5 不为此引入 sessions 表 join（避免未核实的 schema 依赖）。
> **invocation_id 为 NULL 的处理。** 统一用空字符串 `''` 哨兵（`COALESCE(invocation_id,'')`），同一 session 下不同 invocation 各成一行。
> 性能：v5 直接全表聚合 + `HAVING` + `LIMIT`。作业表通常不大、monitor 周期跑，可接受。Phase 2 可加索引预过滤。

**(b) 全量 delivery snapshot** `list_pending_terminal_snapshot(user_id, org_id, session_id, before_ts)`（窄字段、无 limit）：

```sql
SELECT
    id,
    job_id,
    job_name,
    status,
    invocation_id,
    sandbox,
    terminal_at,
    workspace
FROM bohrium_jobs
WHERE user_id = %s AND org_id = %s AND session_id = %s
  AND terminal_at IS NOT NULL AND handled_at IS NULL
  AND terminal_at <= %s
ORDER BY
    (status IN ('failed','stopped')) DESC,   -- 失败优先，喂给 detail_limit 先展开
    terminal_at ASC, submitted_at ASC, id ASC
```

返回本轮 delivery 的权威 job 集合，DB 层必须拿全量 `id/job_id`，不得用 `limit` 作为交付边界。失败/停止排在前，使 context 详情压缩时优先展开失败项（§8）。

**(c) 按 snapshot row ids ack** `mark_handled_by_ids(user_id, org_id, session_id, row_ids)`：

```sql
UPDATE bohrium_jobs
SET handled_at = COALESCE(handled_at, NOW())
WHERE user_id = %s AND org_id = %s AND session_id = %s
  AND id IN (...)
  AND terminal_at IS NOT NULL
  AND handled_at IS NULL
```

`row_ids` 来自 worker run 起点的 `DeliverySnapshot`，confirm 范围与本轮交付权威集合一致。run 中途新完成的作业不在 snapshot 中，不标，留待下次——保证不漏。

**(d) `read_db_now()`** —— `SELECT NOW()`，供 worker run 起点取与 `terminal_at` 同源的 cutoff，避免跨主机时钟偏移。

---

## 5. 决策逻辑（无状态，逐单元）

```python
# unit = scan_delivery_units 的一行聚合
def decide(unit, cfg) -> Reason | None:
    if unit.pending_terminal == 0:
        return None
    # 1) final 最高：active==0 且仍有未交付 → 收尾。
    if unit.active == 0:
        return Reason.FINAL
    # 2) first_failure 快车道：有失败且尚未交付过任何失败。
    if unit.failed_total > 0 and unit.failed_handled == 0:
        return Reason.FIRST_FAILURE
    # 3) progress 里程碑：未交付完成数攒够一段（ceil(total/segments)）→ 汇报一次。
    step = (unit.total + cfg.progress_segments - 1) // cfg.progress_segments  # ceil，total>=1 时恒>=1
    if unit.pending_terminal >= step:
        return Reason.PROGRESS
    return None
```

要点：

- **三条全 ledger 推导。** 无 `now`、无 state 入参。
- **优先级 final > first_failure > progress。** 单 job invocation 直接失败时 `active==0` 先命中，只发 final。
- **progress 上界 N 内生于阈值缩放。** `step = ceil(total/segments)` 随作业数缩放。每次 progress 成功 → ack 抹平 snapshot 中 ≥ step 个 pending → pending 回落到阈值下。因 `segments × ceil(total/segments) ≥ total`，且 final 至少消化 1 个，成功 progress 交付次数 **≤ segments**，与作业数无关。
- **不重复发，无需记账。** final 经 ack `pending_terminal→0`；first_failure 经 ack `failed_handled>0`；progress 经 ack pending 回落到 step 之下。
- **run 未成功不“翻篇”。** busy/error/失败时 pending 不被 ack，下轮重试同一段——重试同一次交付，非新增，有效交付仍 ≤ segments。
- **小批量平滑退化。** `total ≤ segments` 时 `step=1`，每完成 1 个就 push；最后一个走 final，成功 progress ≤ `total-1` ≤ segments。
- **per-invocation 上界。** 以上是单个 scan unit（invocation）的次数。session 级 ack 会把同 session 其他 invocation 的 pending 一并清掉（搭车），只会减少触发次数，不破上界。session 总唤醒 ≤ Σ_invocation (1+segments)。

---

## 6. 触发、合并与并发门（P1-a 修正核心）

`BohriumCompletionScheduler.tick()`：

1. `units = jobs_table.scan_delivery_units(limit=cfg.scan_limit)`，按 `(user_id, org_id, session_id)` 分组。
2. 每个 session：逐单元 `decide(unit, cfg)`，得 eligible 列表；空则跳过。**无状态查询。**
3. **并发门（按序短路，任一不过即跳过该 session 本轮）：**
  - **(a) identity 门**：`get_session(session_id)` 必须存在且当前 `user_id/org_id` 与扫描分组一致；不一致跳过并计入 `skipped_identity`（防御 owner 不一致脏数据，正常恒过）。
  - **(b) status 门**：`if get_session_status(session_id) != 'idle': skip`。**跨进程可见**的真 busy 门——run 进行中/排队中为 `active`/`waiting`，只有 idle 放行。过期 waiting 由 `get_session_status` reconcile 回 idle（既有行为）。
  - **(c) NX 原子占位**：`reservation_key = f"bohrium_delivery:v5:{user_id}:{org_id}:{session_id}:{max_pending_terminal_id}"`（`max_pending_terminal_id = max(unit.max_pending_terminal_id)`）。`if not redis.mark_dedup_key_nx(reservation_key, "1", ttl_sec=cfg.reservation_ttl): skip`。挡同 tick 多实例竞态，且 row-id 高水位避免秒级 `terminal_at` 碰撞压住新完成。短 TTL，无需显式释放。
4. 通过门后**只发一次** trigger_run：
  - `primary reason` = eligible 中优先级最高（FINAL > FIRST_FAILURE > PROGRESS）。
  - `prompt = render_prompt(primary_reason, session_counts, sample_failed_job?)`（§8）。
  - `workspace` = primary 单元的 `workspace`。
  - `res = stream_service.trigger_run(session_id, prompt, origin="bohrium_completion", workspace=workspace, delivery={"notify": primary_reason == FINAL})`（**不传 dedup_key**，占位已由 3c 接管）。
5. 据 `res.status`：
  - `enqueued`：**不记录任何状态。** progress 是否“已发”由 worker ack 隐式表达。
  - `busy`/`error`：跳过，不动 ledger（下轮重试）。

`tick()` 返回 summary（`{"scanned","eligible","triggered","skipped_identity","skipped_busy","errors"}`），**自吞所有异常、绝不抛**（单轮失败返回 `tick_failed=1`）。

> 多实例：identity + status + NX 三门使 **replica>1 也安全**。v5 仍建议 replica=1，占位为防御纵深。
> 已知边界（过载，有界无损）：`get_session_status` 仅在 `status==waiting` 且 queued 标记缺失时 reconcile→idle（sessions_service.py:314）。若 run 队列积压超 queued 标记 TTL（300s）且无存活 run_owner，门可能放行一次重复触发——每 300s 至多一次，重复 run 仅再 ack 一次（at-least-once 已接受）。彻底消除需 Phase 2 长效 inflight 标记。

---

## 7. Worker delivery snapshot 与 ack（顺序关键）

在 `agent_worker.py` 主循环内：

1. **run 起点**（`acquired` 成功后、`run_agent(...)` 前）：
  `snapshot = bohrium_delivery_ack.snapshot(session_id)` —— 解析 `(user_id, org_id)`、`read_db_now()` 取 `before_ts`，并用 §4(b) 查询全部 pending terminal rows。返回不透明 `DeliverySnapshot`：
  ```python
  DeliverySnapshot(
      user_id=..., org_id=..., session_id=..., before_ts=...,
      row_ids=(...),          # confirm 的权威 ack 集合
      job_ids=(...),          # 全量 job_id，context 必须可见
      rows=(...),             # 全量轻字段行(id/job_id/job_name/status/invocation_id/...)，失败优先序
      status_counts={...},
      invocation_counts={...},
  )
  ```
  snapshot 失败返回 `None`（不阻断 run）；成功但无 pending rows 也返回 `None`。
  > snapshot **不预先做 projection**——持全量轻字段，展开几条由 renderer 的 `detail_limit` 决定（§8），职责留在渲染层。
2. **注入 run_agent**：worker 把 `snapshot` 作为可选参数传给 `agent_run_service.run_agent(...)`；`run_agent` 透传给 `build_bohrium_jobs_ports(...)`；port 的 `load_session_jobs` 若持有 snapshot，则据 snapshot 构造 `SessionJobs`（pending 用 snapshot.rows，失败优先序），并带上 `detail_limit`。**注入点 = root run anchor turn 的 `load_session_jobs` 单次调用**（`assembly.py:265-270`）；compaction 时再调返回同一 snapshot，本轮交付边界固定。
3. **run 成功收尾**（`finally` 内、`if acquired:` 块中、**`release_session_run` 之前**，仅 `run_success` 为真）：
  ```python
   if acquired:
       if run_success and snapshot is not None:
           try:
               bohrium_delivery_ack.confirm(snapshot)   # mark snapshot.row_ids handled
           except Exception:
               logger.warning("bohrium ack failed ...", exc_info=True)  # 不阻断 release
       sessions_service.release_session_run(session_id, run_success=run_success)
  ```
  confirm 调 §4(c)，**对所有成功 run 生效**（用户手动消息那次 run 也确认，缺口 2 一并修好）。失败/异常 run 不 confirm。
  > snapshot=None（查询失败或无 pending）时，port 回退现有读侧行为；该 run 不 confirm，pending 留待下轮。

> 顺序保证：confirm 在 status 仍 active 时完成 → status 翻 idle 时 pending 已清零 → 关闭 P1-b 窗口。confirm 异常只记日志，session 最终仍 release。

---

## 8. 交付内容、projection 与 renderer 压缩

delivery snapshot 是本轮交付权威集合；context 是它的 projection。**v5 升级 `session_jobs` renderer**（`session_jobs.py`，格式本是临时、非契约）支持 `detail_limit`，并改 `_RunSessionJobsPort` 在持有 snapshot 时据 snapshot 组装 `SessionJobs`。**不碰 kernel 的决策/编排逻辑**（agent 循环、exp 编排、assembly 调度、compositions 步骤）。

**renderer 两段式（`from_jobs(jobs, *, detail_limit=None)`）：**

- `detail_limit is None`（普通用户轮次）：行为与现状完全一致，`active` / `pending_terminal` 各自全量逐行渲染。
- `detail_limit = M`（completion delivery）：对 `pending_terminal` 与 `active` 各自——前 M 个渲染完整详情行 `pending_terminal_job_i {json}`；其余压成一行溢出摘要
  `pending_terminal_overflow {"count": R, "by_status": {...}, "job_ids": [全量剩余 id]}`。
  active 同理 `active_overflow {...}`。

**projection 硬规则（§12.4）：**

- **全量 job_id 始终可见**：展开的详情行 + 溢出摘要的 `job_ids` 合起来覆盖 snapshot 全量 id。这是“ack 范围 = agent 看到范围”的底线，不可截断。
- 详情展开**失败/停止优先**（snapshot 已失败优先序，前 M 条自然先覆盖失败项）。
- 截断**只影响详情字段**，不影响 id 可见性，更不能 ack 未纳入 snapshot 的 id。

**token 边界（实现须知）：** 第二级压缩压的是详情；**全量 id 列表是 token 底线**，O(单次 pending 数)。几百~几千 id（几 KB~几十 KB）可行；上万级常态下 id 列表本身成瓶颈，需把 id 也分批（一次只交付/ack 一个 batch）——触及 ack 语义，留 Phase 2（§11）。单次 snapshot 大小正常 ≈ `step = ceil(total/N)`（progress 自然分段），突发全完成时 = 当时全量 pending。

`prompt` 承载唤醒原因 + 关键提示：

- **FINAL**：`本会话 invocation 的全部 Bohrium 作业已结束：成功 {succeeded}/{total}，失败 {failed_total}。请汇总结果并给出下一步。`
- **FIRST_FAILURE**：`Bohrium 作业 {job_id}（{job_name}）首次失败（{status}）。另有 {active} 个作业仍在运行。本轮 delivery 还包含同 session 其他 pending terminal jobs，请一起纳入汇总。`（`job_id/job_name/status` 由 scheduler 单独查该单元首个 failed-未 handled 作业获得。）
- **PROGRESS**：`本会话又有 Bohrium 作业完成（已终态 {terminal}/{total}，仍在运行 {active}）。请查看 pending 作业并汇报进度。`

`delivery.notify` 默认仅 FINAL 为 True，可配。progress 次数已被 segments 封顶，notify 频率天然有界。

**交付语义：at-least-once（P2-a）。** snapshot cutoff 保证不漏。`snapshot` 之后进入终态的 job 不在本轮、不被本轮 confirm，下轮再交付。正常实现只基于 snapshot 组装 completion delivery context；若实现异常再次裸查 DB 看到 snapshot 外的 job，它也不被本轮 confirm，最多重复一次，非丢失。

---

## 9. 文件改动一览

**新增：**

- `src/services/bohrium_completion_scheduler.py` —— `BohriumCompletionScheduler.tick()`（惰性依赖、永不抛、返回 summary；可注入 `jobs_table / sessions_service / stream_service / redis / cfg`。**无 state_table、无 now_fn**）。
- `src/services/bohrium_delivery_ack.py` —— `DeliverySnapshot`、`snapshot()`、`confirm()`；snapshot 持全量 `row_ids/job_ids/rows`，confirm 只 ack snapshot row ids。

**修改：**

- `src/dao/bohrium_jobs_table.py` —— 加 `scan_delivery_units(limit)`、`list_pending_terminal_snapshot(...)`、`mark_handled_by_ids(...)`、`read_db_now()`。
- `src/monitor/monitor_worker.py` —— 循环内 `poller.tick()` 后接 `scheduler.tick()`，summary 打日志；循环外各构造一次。
- `src/worker/agent_worker.py` —— run 起点 `snapshot(...)`；传给 `run_agent(...)`；成功收尾 `confirm(...)` 于 `release_session_run` 之前。
- `src/services/agent_run_service.py` —— `run_agent(...)` 接收可选 delivery snapshot，透传给 `build_bohrium_jobs_ports(...)`。
- `src/services/bohrium_jobs_wiring.py` —— `_RunSessionJobsPort` 持有可选 snapshot：有则据 snapshot.rows（失败优先序）构造 `SessionJobs` 并设 `detail_limit`，去掉硬编码 `limit=5`；无 snapshot 时保留现有读侧行为。
- **`matmaster/context/sources/session_jobs.py`** —— `SessionJobsSource.from_jobs` 支持 `detail_limit`：前 M 条详情 + 溢出摘要（含全量 id）；`active`/`pending` 同此压缩。`detail_limit=None` 时行为不变。
- **`matmaster/context/ports.py`** —— `SessionJobs` 加可选 `detail_limit: int | None`（renderer 据此压缩），不改 `SessionJobsPort` Protocol 方法签名。

**不碰：** matmaster kernel 的决策/编排（`agent.py` 循环、`exp.py` 编排、`assembly.py` 调度、`compositions.py` 步骤组合）、`bohrium_jobs` 表结构、poller 引擎、`trigger_run` 实现、`redis_dao`（复用 `mark_dedup_key_nx`）。只升级 `session_jobs` 这个临时 renderer 及其数据对象字段。

**相对 v2 删除（迁移而非兼容）：** `create_bohrium_delivery_state_table.sql`、`bohrium_delivery_state_table.py`。

---

## 10. 配置（默认值，评审可调）

| 配置 | 默认 | 说明 |
|---|---|---|
| `progress_segments` (N) | 3 | 把一个 invocation 的作业切成 N 段，每攒够 `ceil(total/N)` 个未交付完成做一次进度汇报；progress 唤醒次数据此封顶 |
| `delivery_detail_limit` (M) | 20 | completion delivery context 中 `active`/`pending` 各自展开完整详情的条数上界；其余压成溢出摘要（全量 id 仍可见）。普通用户轮次不传，行为不变 |
| `reservation_ttl` | 60s | NX 原子占位 TTL（仅作同 tick 竞态防护）|
| `scan_limit` | 200 | 单轮 `scan_delivery_units` 返回单元上界（最老 pending 优先）|

经环境变量覆盖（如 `BOHRIUM_DELIVERY_PROGRESS_SEGMENTS`、`BOHRIUM_DELIVERY_DETAIL_LIMIT`），沿用 `bohrium_poller._env_int` 模式。

---

## 11. 非目标（Phase 2 deferrals）

- **上万级常态下的 id 分批 delivery**：v5 守“全量 id 始终可见”，单次 delivery 的 token 底线 = O(单次 pending id 数)。几百~几千可行；上万级需放弃单次全量 id、改 batch（一次只交付/ack 一段 id），触及 ack 语义，留 Phase 2。
- 任何持久调度态表 + 周期 reconcile（本设计零持久态）。
- delivery_batches / batch_scopes / `delivery_batch_id` 预留-消费-释放（用 worker 内存 `DeliverySnapshot` 替代，不落表）。
- **按时间的 cooldown / 慢批次中途及时性**：progress 按完成数量切段，慢批次中途反馈迟钝（pull 仍可查）。需要时 Phase 2 加 monitor 进程内存 cooldown 时间戳。
- owner 不一致残留 pending 行的 GC（identity 门跳过，正常不产生）。
- 4 个 policy preset、失败阶梯阈值、heartbeat。
- exactly-once 交付；持久 delivery batch；高通量分页 delivery；跨 run 恢复未 confirm 的 delivery token。
- RUN_END 后 session-local recheck（lean 靠 10s 周期 tick 拾取，first_failure 延迟 ≤ 一个 tick）。

---

## 12. 硬约束（实现须守）

1. **调度器无状态**：唤醒决策仅从 `bohrium_jobs` 当前聚合快照推导，不持久化、不读写任何调度态表，不依赖 `now`。
2. **失败不是无界旁路**：仅首失败有一次性快车道（`failed_handled==0` 推导）；之后失败靠 progress 与 final 覆盖。
3. **非 final 自动唤醒有上界** `≤ 1 + N`（per-invocation，N=`progress_segments`，经 `ceil(total/N)` 阈值 + ack 翻篇保证）；session 总唤醒 ≈ K×(1+N)，与作业数无关。
4. **delivery snapshot 是确认边界 + 全量 id 可见**：DB 层必须读取全量 pending terminal job ids；context 详情可按 `detail_limit` 压缩，但全量 job_id 必须可见，confirm 只能 ack `snapshot.row_ids`。
5. **mark_handled 只发生在 agent 成功消费 snapshot 之后**：poller 不得；trigger enqueued 不得；只有 worker 在 run 成功收尾、**release 之前** confirm。
6. **handled_at = 已纳入一次成功 run 的 delivery snapshot**，非逐 job 深度分析，也非详情一定被展开。
7. **触发前必须过三门**：session identity 一致 + DB `status==idle` + `mark_dedup_key_nx` 原子占位；不得依赖 `try_acquire`/`session_run_queued` 做跨进程互斥。
8. **交付 at-least-once**：snapshot cutoff 保证不漏；snapshot 外窗口内完成的 job 留待下轮。
9. **monitor/worker 两条链路不共享运行态**，只经 ledger 协调；纯 DTO/identity helper 可复用。
10. **renderer 升级不改 kernel 决策/编排**：只动 `session_jobs` 临时 renderer 与 `SessionJobs.detail_limit` 字段；`detail_limit=None` 时普通轮次渲染零变化。

---

## 13. 测试（TDD，沿用 `test_bohrium_poller.py` 风格，假对象注入、不依赖真库）

- `tests/services/test_bohrium_completion_scheduler.py`：注入假 jobs_table + 假 sessions_service（控 `get_session`/`get_session_status`）+ 假 redis + 假 stream_service。**无 state_table、无 now_fn**。覆盖：
  - final / first_failure / progress 命中与优先级（含 active==0 时 final 抢占 first_failure）。
  - **progress 阈值 ceil**：`pending >= ceil(total/segments)` 触发；**专测 `total=5, segments=3`**（step=2，2 次 progress，验证不退化成 4 次）。
  - **progress 上界**：连续 ack 模拟，断言成功 progress 次数 ≤ segments，与 total 取大值无关。
  - first_failure 一次性（`failed_handled>0` 后不命中）。
  - **identity 门**：扫描行 user/org 与当前 session row 不一致时跳过、计 `skipped_identity`。
  - **status 门**：非 idle 跳过。
  - **NX 占位**：key 用 `max_pending_terminal_id`；返回 False 跳过；同 session 两单元只发一次。
  - session 级合并：多 eligible 单元一次 trigger_run，按 primary reason。
  - `enqueued` 后不写任何持久状态；`busy/error` 不动 ledger。
  - `tick()` 自吞异常返回 `tick_failed=1`。
- `tests/services/test_bohrium_delivery_ack.py`：`snapshot` 调 `read_db_now()` 返回全量 row/job ids（失败优先序）；失败或空集合返回 None 不抛；`confirm` 只按 `snapshot.row_ids` 调 `mark_handled_by_ids`；confirm 异常不阻断 release。
- `tests/context/test_session_jobs_source.py`：**新增** —— `from_jobs(detail_limit=None)` 与现状逐行一致；`detail_limit=M` 时前 M 条详情 + 溢出摘要，且摘要 `job_ids` 与详情行合起来覆盖全量 id；active/pending 各自压缩；M ≥ 总数时无溢出行。
- `tests/services/test_bohrium_jobs_wiring.py`：带 snapshot 时 `_RunSessionJobsPort` 据 snapshot.rows 构造 `SessionJobs`（失败优先、带 detail_limit），不再裸查 `limit=5` 定交付集合；无 snapshot 时读侧不变。
- `tests/worker`：断言 snapshot 在 acquire 后、`run_agent` 前创建并传入；run 成功时 confirm 在 release 前；失败不 confirm；confirm 抛异常仍 release。
- `tests/dao/`（有真库 fixture 才跑）：`scan_delivery_units` 聚合/排序/limit；`list_pending_terminal_snapshot` 返回 cutoff 前全量 pending（失败优先、无 fixed limit）；`mark_handled_by_ids` 只标 snapshot ids。
- `tests/monitor/test_monitor_worker.py`（已存在）：补 scheduler.tick 接入后循环正常、单轮异常不退出。

---

## 14. 端到端时序示例

**交互式 3 个 DFT 作业，第 1 个失败（segments=3 → step=ceil(3/3)=1，detail_limit=20 全展开）：**

```
作业1 失败终态 → poller 写 ledger
scheduler.tick: unit(active=2, failed_total=1, failed_handled=0, pending=1) → FIRST_FAILURE
  → identity 过 + status==idle 过 + NX 占位过 → trigger_run(prompt="作业1 失败…", notify=False) → enqueued
worker: run 起点 DeliverySnapshot(row_ids=[job1], job_ids=[job1])
  → port 据 snapshot 组装 SessionJobs → renderer 全展开 job1 → agent 分析
  → 成功：先 confirm(snapshot.row_ids，failed_handled→1) 再 release(idle)
作业2 完成 → unit(active=1, pending=1, failed_handled=1, total=3)
  → first_failure 已交付 → pending(1) >= step(1) → PROGRESS → 触发、确认(ack job2)
作业3 完成 → unit(active=0, pending=1) → FINAL → 触发(notify=True)、确认 → pending=0
```

**1000 个作业陆续完成（lean 上界 + 压缩验证，segments=3 → step=ceil(1000/3)=334，detail_limit=20）：**

```
第1个失败 → FIRST_FAILURE（1 次）
累计 334 未交付 → PROGRESS → snapshot 含 334 行
  → renderer：前 20 条详情(失败优先) + 1 行 pending_terminal_overflow{count:314, job_ids:[全量314个]}
  → confirm ack 334 → pending 回 0
再攒 334 → PROGRESS → 同上
剩余 ~331 全部完成 → active=0 → FINAL → snapshot/ack
合计 1(first_failure) + 2(progress) + 1(final) = 4 次；progress ≤ segments=3。
单次 context：20 条详情 + 314 个 id（约几 KB），token 可控。
阈值 step 随 total 缩放（10000 作业 → step=3334，单次 ~3334 个 id ≈ 数十 KB，接近 token 底线上沿；
更大规模需 Phase 2 的 id 分批 delivery）。
```

（progress 按完成数量切段，无 budget 计数、无 cooldown、无状态表；详情压缩守住全量 id 可见；唯一让掉的是慢批次按时间响应，见 §11。）
