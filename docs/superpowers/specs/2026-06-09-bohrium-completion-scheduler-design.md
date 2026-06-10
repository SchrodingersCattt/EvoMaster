# Bohrium 作业完成调度器（无状态闭环）设计

> 状态：设计稿 —— 已整合两轮交叉评审决议；本文自足，不保留修订版本记录（演进过程见 git 历史）。
> 范围：精简闭环优先（lean），显式覆盖几百~几千作业单批；调度单元为 invocation 级判定 + session 级合并；delivery snapshot 与 ack 落 worker；context 详情可压缩，全量 job_id 始终可见。
> 非目标与已接受限制：见 §11。

---

## 1. 目标

在已跑通的 Bohrium job ledger 与 poller 之上补齐 agent 唤醒闭环：某批作业进入终态时，按策略唤醒一次 agent run 去消费结果；run 成功后确认交付，避免同一批作业被无限重复呈现。

调度器只回答：**当前这些已终态、尚未交付给 agent 的作业，是否值得唤醒一次 agent run？** 它不 poll 平台，不分析结果，不持有任何跨 tick 状态。

成本硬约束：

```
poller 刷新 job 状态可以按 job 频繁做；
agent run 唤醒必须按批次、按里程碑、按背压做。
任一 invocation 的非 final 自动唤醒上界 = 1(first_failure) + N(progress)，与作业数无关。
（per-invocation 上界；K 个 invocation 的 session 非 final 总唤醒 ≤ K×(1+N)，
 连同每 invocation 至多一次 final，总唤醒 ≤ K×(2+N)，均与作业数无关。）
```

---

## 2. 背景：当前代码事实（已对 codex/provider-stage1 源码核实）

设计直接依赖以下事实，均已逐条核实行号；后文决策回指这里。

- **数据层完整。** `bohrium_jobs`（`src/sql/create_bohrium_jobs_table.sql`）一行一作业。主键 `id BIGINT AUTO_INCREMENT`；`(user_id, org_id, sandbox, job_id)` 是唯一键 `uk_owner_job_id`（:4,:34）。带 `session_id / invocation_id(nullable) / spawn_id(nullable) / status / terminal_at / handled_at / submitted_at`。CHECK 约束保证状态机：活跃态 `next_poll_at` 非空且 `terminal_at` 空；终态反之；`handled_at` 必先终态。索引含 `idx_session_pending(user_id, org_id, session_id, handled_at, terminal_at)`。
- **DAO 是唯一写入口**（`src/dao/bohrium_jobs_table.py`）。已有 `apply_poll / mark_handled / query_session_pending_terminal(limit=5) / claim_due_batch`。`mark_handled` 幂等但**生产代码从未调用**。本设计不复用 `limit=5` 作为 delivery 边界，新增全量 snapshot 查询（字段对齐现状 agent 投影）。
- **Poller 链路已通。** `BohriumMonitor`（`src/services/bohrium_poller.py:173`）的 `tick()`（:193）→ claim → 查平台 → `apply_poll`。`src/monitor/monitor_worker.py` 每 10s 调一次 `tick()`（`MONITOR_TICK_INTERVAL` 默认 10）。poller 只写 ledger。
- **触发接口现成。** `ChatStreamService.trigger_run(session_id, prompt, *, origin, dedup_key, delivery, on_busy, workspace, dedup_ttl_sec)`（stream_service.py:353）→ `TriggerResult(status ∈ enqueued|deduped|busy|error)`。`prompt` 写成一条 System `trigger` 事件作为本轮输入。`on_busy` 是已声明但未被读取的参数，busy 一律原样返回调用方（等价 skip），scheduler 不当它可调互斥。`get_stream_service()`（:895）模块级工厂。
- **触发的副作用顺序（NX fail-closed 决策依据）。** `_prepare_run` 占锁成功后才写 System `trigger` 事件（busy 短路在写事件之前，busy 路径无残留）；`_enqueue_run`（:340）内排队通知 `_notify_run_queued`（:346）先于 `lpush_agent_run_job`（:347）执行，lpush 失败的回滚只恢复 status 与 queued 标记（:348-349），**不回滚已写历史事件、撤不回已发通知**。NX 占位与 run 队列共用同一 Redis：Redis 故障时入队必然失败。
- **触发的并发事实。** `_prepare_run`（:241，:269 为其内部 acquire 调用行）→ `try_acquire_session_run`（sessions_service.py:461）：仅本进程内存集合 + 无条件 DB UPDATE（chat_sessions_table.py:244 不检查当前状态，行存在即恒 True），跨进程不互斥；`_enqueue_run`（:340）同步 `set_session_status('waiting')`（:343）。`get_session_status`（sessions_service.py:335）返回 `idle|active|waiting|failed` 且把过期 waiting reconcile 回 idle（:314），**跨进程可见**。
- **run 失败的状态事实（失败语义依据）。** `release_session_run(run_success=False)` 把会话状态置 `failed`（sessions_service.py:494）。`failed` 没有自动复位路径：`reset_stale_active_sessions`（:307）只复位 `active` 且当前无任何调用点（死代码，部署时自动复位并不存在），waiting reconcile（:314）只处理 `waiting`；唯一复位点是下一次 `try_acquire_session_run`（即用户再次发消息等任何新 run）。用户 run 失败与触发 run 失败走同一路径，状态机不区分 origin。
- **Redis NX 的可用性事实。** `mark_dedup_key_nx`（redis_dao.py:352）底层 `SET NX EX`；但无 Redis client 或异常时同样返回 False，与「key 已被占位」**不可区分**。区分这两种 False（用于 fail-closed 的计数与告警）须新增三态方法（§6c/§9）。
- **交付读侧已存在、数据/渲染分层。** agent run 时 `agent_run_service.py:515-521` 调 `build_bohrium_jobs_ports(...)` 构造 `_RunSessionJobsPort`（`src/services/bohrium_jobs_wiring.py:144-211`，service 层，内部硬编码 `limit=5` 查 DB，:168）。kernel 侧 `_load_jobs_or_empty`（assembly.py:265-270）有**三个调用点**：root turn 的 anchor 分支（:192）与 continuation 分支（:200），以及 compaction（:237）。root run 每次恰好走其中一个 turn 分支调用一次（exp.py:602）；走 anchor 还是 continuation 由 user_instructions hash 是否变化决定（turn_intent.py:25-27）——completion 触发的 run 因指令通常未变，**多数走 continuation 分支**。渲染由 `SessionJobsSource.from_jobs`（session_jobs.py:21-33）完成，JSON-line 格式自注明 intentionally temporary、非 product contract（:13-17）；其**唯一调用点**是 `compositions.py:87`，调用时不传任何额外参数——因此 detail_limit 只能经 `SessionJobs` 数据对象字段传递，不能改函数签名（§8）。
- **Worker 收尾点。** `agent_worker.py` `finally` 内：先 `delete_interaction_run_active`，再 `if acquired: release_session_run(run_success=...)`（:512-515）→ 置 DB idle/failed。
- **delivery notify 契约两端现成。** `trigger_run(delivery={"notify": bool})` 与 worker 侧 `_should_notify_completion`（agent_worker.py:76-80）、`DeliverySpec`（models/chat.py:365-368）吻合：缺省发通知，notify=False 静默。
- **owner 可变是受支持操作。** `set_session_bohrium`（sessions_service.py:384）允许会话中途改 org_id。owner 不一致是正常会出现的输入而非仅脏数据，扫描须在 SQL 层过滤（§4a），identity 门（§6a）只兜竞态窗口。

### 当前两个缺口

1. **触发缺口**：无任何东西在作业终态时唤醒 session。
2. **确认缺口**：`mark_handled` 从未被调用 → 终态作业永停 pending，每轮被 `limit=5` 渲染反复呈现最老 5 个。经 worker delivery snapshot + confirm 修复，确认范围以 snapshot 的全量 row ids 为准。

---

## 3. 架构：两条链路，ledger 状态驱动协调

两条链路各自落在已有进程内，**只通过 ledger 状态协调**（无任何额外调度态表），**不共享运行态**：

```
Monitor 进程（已有循环，每轮）
  ├─ poller.tick()      → 查平台、写 ledger（置 terminal_at）              [已实现]
  └─ scheduler.tick()   → 聚合扫描 → 逐 (session,invocation) 无状态判定 → 按 session 合并
                          → [identity 门 + status==idle 门 + NX 占位(fail-closed)] → trigger_run [新增]
                          （判定纯 ledger 聚合，不读写任何持久态）

Worker 进程（已有 run 完成路径；对所有 run 生效，不分 origin）
  ├─ run 起点：查询全量 pending terminal rows（查询执行瞬间即交付边界）
  │           → DeliverySnapshot(row_ids, job_ids, rows, counts)           [新增]
  │           → 传入 run_agent → port：pending 据 snapshot（带 detail_limit），active 实时查
  └─ run 成功收尾：先 confirm(snapshot) 按 snapshot.row_ids mark_handled，
                  再 release_session_run                                    [新增，顺序关键]
```

唤醒是 **attempt（monitor）**，交付确认是 **confirm（worker，RUN_END）**，两者分离。

**为什么 ack 必须在 worker、且在 release 之前：** context 渲染条件是 `terminal_at IS NOT NULL AND handled_at IS NULL`。monitor 若在发起时就 `mark_handled`，被唤醒的 run 渲染到空 pending，等于把要交付的东西标没了。`handled_at` 必须在 agent 成功消费本轮 delivery snapshot 之后才能置——只有 worker 知道 run 何时跑完。又因为跨进程 busy 门用 DB status，**confirm 必须在 `release_session_run` 置 idle 之前完成**：否则存在 release→tick→（monitor 见 idle+pending 重新触发）→ack 的竞态窗口。

**为什么无状态闭环自洽：** ack 同时是交付确认 + 进度里程碑的「翻篇」。run 成功后 ack 抹平 snapshot 中的 pending，`pending_terminal` 回落到阈值之下，下一轮 `decide` 不再对同一段触发；progress 不需要任何「已发过几次」的持久记录。

**失败语义两分（显式决策）：**

- **trigger 级失败**（`trigger_run` 返回 busy/error，run 未启动）：pending 不动，下轮 tick 重试同一段——重试同一次交付，非新增。busy 路径无任何残留（占锁失败先于写事件，§2）；error=enqueue_failed 已回滚 status 与 queued 标记，但残留一条孤儿 trigger 事件与一次排队通知（§6 已知边界四，仅 Redis 瞬断窗口出现，有界）。
- **run 级失败**（run 启动后未成功）：不 confirm、pending 不动，但 release 把状态置 `failed`（§2），status 门此后跳过该 session，**自动交付停摆**；由下一次用户交互自愈——任何新 run 成功收尾即全量 confirm 清掉积压。本设计不做失败自动重试，这同时消除了确定性失败 run 被每 tick 重触发的无界开销，是有意取舍（§11）。

---

## 4. 数据模型：不新增任何表

**零新增表、零持久调度态。** 所有判定量从 `bohrium_jobs` 实时聚合。在 `BohriumJobsTable` 加四个方法。

**(a) 聚合扫描** `scan_delivery_units(limit)`（显式 limit + 确定序，最老 pending 优先防饥饿）：

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
WHERE EXISTS (
    SELECT 1 FROM evo_chat_sessions s
    WHERE s.session_id = bohrium_jobs.session_id
      AND s.user_id    = bohrium_jobs.user_id
      AND s.org_id     = bohrium_jobs.org_id
)
GROUP BY user_id, org_id, session_id, COALESCE(invocation_id, '')
HAVING pending_terminal > 0
ORDER BY first_pending_terminal_at ASC, user_id ASC, org_id ASC,
         session_id ASC, invocation_key ASC
LIMIT %s
```

扫描已按当前 session owner 过滤（EXISTS 子查询，列名 `evo_chat_sessions.user_id/org_id` 已核实存在）；scheduler 触发前仍用当前 session row 再校验一次 identity（§6a，竞态兜底）。`total` 是该单元全部作业数，progress 阈值据此缩放。`max_pending_terminal_id` 仅用于 Redis NX 占位 key 高水位，不进业务语义。

> **owner 过滤语义。** owner 不一致的 pending 行有两个来源：`set_session_bohrium` 中途切 org（受支持操作，§2）与异常脏数据。这类行必须在 SQL 层用 EXISTS 排除：若只靠 Python 门跳过，最老优先排序会让它们永久占据队首，累积到 `scan_limit` 个即把全部正常 session 饿死成全局停摆——故不接受把过滤留到 Phase 2。identity 门（§6a）保留，仅兜扫描到触发之间 owner 又变更的竞态窗口。残留行不再影响调度、仅占存储，归档/GC 留 Phase 2。
> **invocation_id 为 NULL 的处理。** 统一用空字符串 `''` 哨兵（`COALESCE(invocation_id,'')`），同一 session 下不同 invocation 各成一行。
> **性能。** 全表聚合 + 相关子查询 + `HAVING` + `LIMIT`，monitor 周期跑，当前量级可接受（EXISTS 走 evo_chat_sessions 的 session_id 索引点查）。注意表只增不减（handled 行不归档）；量级增长后的自然演进是两阶段扫描——先经 `idx_session_pending` 找出存在 pending 的键集，再仅对键集做全量聚合——连同归档治理一并记 Phase 2（§11）。另该索引列序 `(..., handled_at, terminal_at)` 对 pending 谓词只能部分利用，Phase 2 调索引时一并处理。

**(b) 全量 delivery snapshot** `list_pending_terminal_snapshot(user_id, org_id, session_id)`（无 limit、字段对齐现状投影）：

```sql
SELECT
    id,
    invocation_id,
    terminal_at,
    job_id, job_name, status, sandbox, project_id, input_dir, workspace,
    submitted_at, last_polled_at, result_dir
FROM bohrium_jobs
WHERE user_id = %s AND org_id = %s AND session_id = %s
  AND terminal_at IS NOT NULL AND handled_at IS NULL
ORDER BY
    (status IN ('failed','stopped')) DESC,   -- 失败优先，喂给 detail_limit 先展开
    terminal_at ASC, submitted_at ASC, id ASC
```

返回本轮 delivery 的权威 job 集合，DB 层必须拿全量 `id/job_id`，不得用 `limit` 作为交付边界。字段集 = 现状 `_AGENT_COLUMNS`（bohrium_jobs_table.py:43，含 `result_dir` 等取结果所需字段）+ `id/invocation_id/terminal_at`，保证展开的详情行信息量与现状逐 job 一致，换源不造成字段回归。查询执行瞬间即交付边界：`terminal_at` 由 `apply_poll` 的 DB `NOW()` 写入，无需独立 cutoff 参数；run 中途新终态的行天然不在结果集，留待下轮。失败/停止排在前，使 context 详情压缩时优先展开失败项（§8）。

**(c) 按 snapshot row ids ack** `mark_handled_by_ids(user_id, org_id, session_id, row_ids)`：

```sql
UPDATE bohrium_jobs
SET handled_at = NOW()
WHERE user_id = %s AND org_id = %s AND session_id = %s
  AND id IN (...)
  AND terminal_at IS NOT NULL
  AND handled_at IS NULL
```

幂等性由 `handled_at IS NULL` 谓词保证（重复 ack 是 no-op）。`row_ids` 来自 worker run 起点的 `DeliverySnapshot`，confirm 范围与本轮交付权威集合一致；run 中途新完成的作业不在 snapshot 中，不标，留待下次——保证不漏。实现注意：`row_ids` 上千时按批（如每批 500）分块执行。

**(d) 首个未交付失败作业** `get_first_pending_failed(user_id, org_id, session_id, invocation_key)`，供 FIRST_FAILURE prompt 取 `job_id/job_name/status`（§8）：

```sql
SELECT job_id, job_name, status
FROM bohrium_jobs
WHERE user_id = %s AND org_id = %s AND session_id = %s
  AND COALESCE(invocation_id, '') = %s
  AND status IN ('failed','stopped')
  AND handled_at IS NULL
ORDER BY terminal_at ASC, id ASC
LIMIT 1
```

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
- **progress 上界 N 内生于阈值缩放。** 每次成功 progress 经 ack 至少消化 `step = ceil(total/segments)` 个 pending；因 `step ≥ total/segments`，成功 progress 次数 ≤ `total/step` ≤ segments，与作业数无关。
- **不重复发，无需记账。** final 经 ack `pending_terminal→0`；first_failure 经 ack `failed_handled>0`；progress 经 ack pending 回落到 step 之下。
- **trigger 级失败下轮重试；run 级失败停摆。** busy/error 时 pending 不动、下轮重试同一段（重试同一次交付，非新增，有效交付仍 ≤ segments）；run 启动后失败则 session 进入 `failed`，自动交付停摆至下一次用户交互（§3/§6/§11）。
- **小批量平滑退化。** `total ≤ segments` 时 `step=1`，每完成 1 个就 push；最后一个走 final，成功 progress ≤ `total-1` ≤ segments。
- **per-invocation 上界。** 以上是单个 scan unit（invocation）的次数。session 级 ack 会把同 session 其他 invocation 的 pending 一并清掉（搭车），只会减少触发次数，不破上界。session 非 final 总唤醒 ≤ Σ_invocation (1+segments)。

---

## 6. 触发、合并与并发门

`BohriumCompletionScheduler.tick()`：

1. `units = jobs_table.scan_delivery_units(limit=cfg.scan_limit)`，按 `(user_id, org_id, session_id)` 分组。
2. 每个 session：逐单元 `decide(unit, cfg)`，得 eligible 列表；空则跳过。**无状态查询。**
3. **并发门（按序短路，任一不过即跳过该 session 本轮）：**
  - **(a) identity 门**：`get_session(session_id)` 必须存在且当前 `user_id/org_id` 与扫描分组一致；不一致跳过并计 `skipped_identity`（扫描已在 SQL 层过滤，此门只兜扫描到触发之间 owner 又变更的竞态窗口，§4a）。
  - **(b) status 门**：`status = get_session_status(session_id)`；**仅 `idle` 放行**。`active`/`waiting` 计 `skipped_busy`；`failed` 计 `skipped_failed`——failed 即停摆（§3）。**实现要求**：tick summary 中 `skipped_failed > 0` 时打 WARN 并附 session 清单——这是停摆唯一的发现通道，不得只埋在 INFO summary 里。过期 waiting 由 `get_session_status` reconcile 回 idle（既有行为）。
  - **(c) NX 原子占位（fail-closed）**：`reservation_key = f"bohrium_delivery:{user_id}:{org_id}:{session_id}:{max_pending_terminal_id}"`（`max_pending_terminal_id = max(unit.max_pending_terminal_id)`）。调新增三态方法 `try_reserve_nx(key, "1", ttl_sec=cfg.reservation_ttl)`：返回 `False`（已被占位）→ skip；返回 `None`（无 Redis client 或异常）→ **同样 skip**，计 `skipped_redis`，按 tick 聚合打一条 WARN（不逐 session 刷日志）。fail-closed 依据 §2 副作用顺序事实：NX 与 run 队列共用同一 Redis，Redis 故障时 lpush 必然失败，放行产不出可用 run，只会每 tick 每 session 写一条孤儿 trigger 事件 + 发一次排队通知 + DB 状态空转；skip 是背压而非关停，Redis 恢复后下轮 tick 自动续上（pending 仍在 ledger）。NX 是防同 tick 多实例竞态的防御纵深，主互斥是 status 门。row-id 高水位避免秒级 `terminal_at` 碰撞压住新完成作业。短 TTL，无需显式释放。
4. 通过门后**只发一次** trigger_run：
  - `primary reason` = eligible 中优先级最高（FINAL > FIRST_FAILURE > PROGRESS）。
  - `prompt = render_prompt(primary_reason, session_counts, first_failed_job?)`（§8；FIRST_FAILURE 时经 §4d 单查一次）。
  - `workspace` = primary 单元的 `workspace`。已知限制：多 invocation 不同 workspace 合并时只取 primary 的，其余 invocation 的产物路径不在本次 run 的 workspace 下（作业级信息仍在 context 行内可见）。
  - `res = stream_service.trigger_run(session_id, prompt, origin="bohrium_completion", workspace=workspace, delivery={"notify": primary_reason == FINAL})`（**不传 dedup_key**，占位已由 3c 接管）。
5. 据 `res.status`：
  - `enqueued`：**不记录任何状态。** progress 是否「已发」由 worker ack 隐式表达。
  - `busy`/`error`：跳过，不动 ledger（下轮重试；NX key 未过期时至多延迟一个 TTL）。

`tick()` 返回 summary（`{"scanned","eligible","triggered","skipped_identity","skipped_busy","skipped_failed","skipped_redis","errors"}`），**自吞所有异常、绝不抛**（单轮失败返回 `tick_failed=1`）。

> 多实例：identity + status + NX 三门使 replica>1 也安全（Redis 正常时）；Redis 故障时统一 skip（fail-closed），不存在退化重复触发。仍建议 replica=1，占位为防御纵深。
> **已知边界一（queued 标记过期）**：`get_session_status` 仅在 `status==waiting` 且 queued 标记缺失时 reconcile→idle。若 run 队列积压超 queued 标记 TTL（300s）且无存活 run_owner，门可能放行一次重复触发——每 300s 至多一次，重复 run 仅再 ack 一次（at-least-once 已接受）。
> **已知边界二（check-then-act 窗口）**：status 门读到 idle 与 trigger_run 入队之间存在毫秒级窗口，用户消息可插入（用户路径不查 status 门，`try_acquire` 又是无条件 UPDATE，§2）。最坏并发双 run → 双 snapshot → 重复交付一次，confirm 幂等，无丢失。
> **已知边界三（worker crash 残留 active）**：run 进行中 worker 进程被强杀时状态停在 `active`。现存唯一复位通道是用户打开会话时 subscribe 流的 stale 检测（sessions_service.py:542）——`reset_stale_active_sessions`（:307）当前是无调用点的死代码，部署时自动复位并不存在。无人观察的 session 因此调度停摆。最小修法是 worker 启动路径调一次该方法（仅单 worker 副本部署下安全）；更稳的 run_owner 存活探测复位留 Phase 2；本版接受（§11）。
> **已知边界四（Redis 瞬断的孤儿 trigger 事件）**：trigger 事件在占锁成功后、入队前写入历史，排队通知先于 lpush（§2 副作用顺序事实）。NX 检查通过后 Redis 恰好故障时，本次触发残留一条不执行的 System trigger 事件 + 一次排队通知（status 与 queued 标记由 `_enqueue_run` 回滚）。fail-closed 已把该残留压缩到状态翻转的瞬断窗口、每次故障至多一条，有界、接受；根治（事件写入后置到入队成功之后，或失败补偿删除）触及 `trigger_run` 实现，留 Phase 2（§9 不碰清单不变）。

---

## 7. Worker delivery snapshot 与 ack（对所有 run 生效，顺序关键）

snapshot/confirm **不区分 run 的 origin**：用户消息 run 与 completion 触发 run 走同一条路径。这是「ack 范围 = agent 看到范围」不变量的要求，也使缺口 2 对用户轮次一并闭合（用户 run 消费过的 pending 同样翻篇）。

在 `agent_worker.py` 主循环内：

1. **run 起点**（`acquired` 成功后、`run_agent(...)` 前）：
  `snapshot = bohrium_delivery_ack.snapshot(session_id)` —— 解析 `(user_id, org_id)`（取 session row；org 未绑定 → None），用 §4(b) 查询全部 pending terminal rows（查询执行瞬间即本轮交付边界）。返回不透明 `DeliverySnapshot`：
  ```python
  DeliverySnapshot(
      user_id=..., org_id=..., session_id=...,
      row_ids=(...),          # confirm 的权威 ack 集合
      job_ids=(...),          # 全量 job_id，context 必须可见
      rows=(...),             # 全量行：现状 _to_agent_job 同构投影 + id/invocation_id/terminal_at，失败优先序
      status_counts={...},
      invocation_counts={...},
  )
  ```
  查询失败返回 `None`（不阻断 run）；成功但无 pending rows 也返回 `None`。
  > snapshot **不预先做截断**——持全量行，展开几条由 renderer 按 `detail_limit` 决定（§8），职责留在渲染层。行字段与现状 `_to_agent_job` 投影同构（含 `result_dir` 等取结果字段），换源不造成字段回归。
2. **注入 run_agent**：worker 把 `snapshot` 作为可选参数传给 `agent_run_service.run_agent(...)`；`run_agent` 透传给 `build_bohrium_jobs_ports(...)`。port 的 `load_session_jobs`：
  - **pending**：持有 snapshot 时据 `snapshot.rows`（失败优先序）构造，并在 `SessionJobs` 上设 `detail_limit`。本轮交付边界固定——root turn（anchor 或 continuation 分支，§2）调用一次，compaction 再调时返回**同一** snapshot 的 pending。
  - **active**：每次实时查 `query_session_active`。snapshot 只钉死 pending（ack 边界要求）；active 的语义是「当前还在跑什么」，钉死反而与 FIRST_FAILURE prompt 的在跑计数矛盾。
  - snapshot 为 None 时整体回退现有读侧行为（`limit=5` 查询、`detail_limit=None`）。
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
  confirm 调 §4(c)。失败/异常 run 不 confirm（pending 保留；后续见 §3 失败语义两分）。

> 顺序保证：confirm 在 status 仍 active 时完成 → status 翻 idle 时 pending 已清零，关闭 idle-before-ack 竞态。confirm 异常只记日志，session 最终仍 release；未 ack 的 pending 由下一轮（调度触发或用户消息）重新交付，at-least-once。
> **用户轮次的行为变更（显式承认）**：有 pending 时，用户 run 的 pending 视图从「最老 5 条详情」变为「前 M 条详情 + 全量 id 溢出摘要」，且成功后全量 confirm；展开行的字段与现状逐 job 一致（§4b 字段对齐），变化只在条数与溢出摘要。「渲染与现状一致」的保证只对无 pending 的会话成立（snapshot=None 回退路径）。
> **notify 被用户 run 吞掉（已知行为）**：用户消息抢在 scheduler tick 之前到达并成功收尾时，pending 已被消化，FINAL 触发连同其 notify 不再发生——信息已在该 run 的 context 中交付，符合 at-least-once；产品上接受（§11）。

---

## 8. 交付内容、projection 与 renderer 压缩

delivery snapshot 是本轮交付权威集合；context 是它的 projection。升级 `session_jobs` renderer（格式本是临时、非契约，§2）支持详情压缩。**不碰 kernel 的决策/编排逻辑**（agent 循环、exp 编排、assembly 调度、compositions 步骤），`from_jobs` 签名与唯一调用点 `compositions.py:87` 均不动。

**传递机制：`SessionJobs` 加可选字段 `detail_limit: int | None = None`**（ports.py），由 wiring 在持有 snapshot 时设置；renderer 读字段决定是否压缩。

**renderer 两段式（`from_jobs` 读 `jobs.detail_limit`）：**

- 字段为 `None`（无 snapshot 的回退路径）：行为与现状完全一致，`active` / `pending_terminal` 各自全量逐行渲染。
- 字段为 `M`：对 `pending_terminal` 与 `active` 各自——前 M 个渲染完整详情行 `pending_terminal_job_i {json}`；其余压成一行溢出摘要
  `pending_terminal_overflow {"count": R, "by_status": {...}, "job_ids": [全量剩余 id]}`。
  active 同理 `active_overflow {...}`。

**projection 硬规则：**

- **全量 job_id 始终可见**：展开的详情行 + 溢出摘要的 `job_ids` 合起来覆盖 snapshot 全量 id。这是「ack 范围 = agent 看到范围」的底线，不可截断。
- 详情展开**失败/停止优先**（snapshot 已失败优先序，前 M 条自然先覆盖失败项）。
- 截断**只影响详情字段**，不影响 id 可见性，更不能 ack 未纳入 snapshot 的 id。
- 溢出摘要的 `job_ids` **不按 job_id 去重**：唯一键含 `sandbox`，同一 `job_id` 可能以不同 sandbox 各占一行，计数与 ack 一律以 row 为准。

**token 边界（实现须知）：** 压缩压的是详情；**全量 id 列表是 token 底线**，O(单次 pending 数)。几百~几千 id（几 KB~几十 KB）可行；上万级常态下 id 列表本身成瓶颈，需把 id 也分批（一次只交付/ack 一个 batch）——触及 ack 语义，留 Phase 2（§11）。单次 snapshot 大小正常 ≈ `step = ceil(total/N)`（progress 自然分段），突发全完成时 = 当时全量 pending。

`prompt` 承载唤醒原因 + 关键提示：

- **FINAL**：`触发批次的全部 Bohrium 作业已结束：成功 {succeeded}/{total}，失败 {failed_total}。请汇总结果并给出下一步。`
- **FIRST_FAILURE**：`Bohrium 作业 {job_id}（{job_name}）首次失败（{status}），另有 {active} 个作业仍在运行。`（`job_id/job_name/status` 经 §4(d) 查询。）
- **PROGRESS**：`本会话又有 Bohrium 作业完成（已终态 {terminal}/{total}，仍在运行 {active}）。请汇报进度。`
- **三条 prompt 统一追加结尾句**：`本轮交付为 session 级：context 中全部 pending_terminal 详情行与溢出 job_ids 均在本次确认范围内，请一并查看处理。`——snapshot/ack 是 session 级而 primary reason 只代表一个 invocation，缺这句时 FINAL/PROGRESS 唤醒会把其他 invocation 的 pending 一并 ack 掉却未指示 agent 处理。
- prompt 中的计数取自 tick 时刻聚合，run 实际执行时可能已漂移（排队、用户插队消费）；context 行才是权威，文案不做绝对化承诺。

`delivery.notify` 默认仅 FINAL 为 True，可配。progress 次数已被 segments 封顶，notify 频率天然有界。

**交付语义：at-least-once。** snapshot 边界（查询执行瞬间）保证不漏。snapshot 之后进入终态的 job 不在本轮、不被本轮 confirm，下轮再交付。重复交付至多一次且 confirm 幂等，非丢失。

---

## 9. 文件改动一览

**新增：**

- `src/services/bohrium_completion_scheduler.py` —— `BohriumCompletionScheduler.tick()`（惰性依赖、永不抛、返回 summary；可注入 `jobs_table / sessions_service / stream_service / redis / cfg`。无 state_table、无 now_fn）。
- `src/services/bohrium_delivery_ack.py` —— `DeliverySnapshot`、`snapshot()`、`confirm()`；snapshot 持全量 `row_ids/job_ids/rows`，confirm 只 ack snapshot row ids。

**修改：**

- `src/dao/bohrium_jobs_table.py` —— 加 `scan_delivery_units(limit)`（含当前 owner 的 EXISTS 过滤）、`list_pending_terminal_snapshot(...)`（字段对齐 `_AGENT_COLUMNS`）、`mark_handled_by_ids(...)`、`get_first_pending_failed(...)`。
- `src/dao/redis_dao.py` —— 新增三态 `try_reserve_nx(key, value, ttl_sec) -> bool | None`（True=占位成功 / False=已被占位 / None=无 client 或异常；None 由 scheduler 按 fail-closed skip 处理，§6c）；现有方法不动。
- `src/monitor/monitor_worker.py` —— 循环内 `poller.tick()` 后接 `scheduler.tick()`，summary 打日志；循环外各构造一次。
- `src/worker/agent_worker.py` —— run 起点 `snapshot(...)`；传给 `run_agent(...)`；成功收尾 `confirm(...)` 于 `release_session_run` 之前。
- `src/services/agent_run_service.py` —— `run_agent(...)` 接收可选 delivery snapshot，透传给 `build_bohrium_jobs_ports(...)`。
- `src/services/bohrium_jobs_wiring.py` —— `_RunSessionJobsPort` 持有可选 snapshot：pending 据 snapshot.rows（失败优先序）构造并设 `SessionJobs.detail_limit`，active 保持实时查询；`limit=5` 不再充当交付边界，仅存于无 snapshot 的回退路径（行为与现状一致）。
- `matmaster/context/sources/session_jobs.py` —— `from_jobs` 读 `jobs.detail_limit`：前 M 条详情 + 溢出摘要（含全量 id）；`active`/`pending` 同此压缩；`None` 时行为不变。
- `matmaster/context/ports.py` —— `SessionJobs` 加可选 `detail_limit: int | None = None`；不改 `SessionJobsPort` Protocol 方法签名。

**不碰：** matmaster kernel 的决策/编排（`agent.py` 循环、`exp.py` 编排、`assembly.py` 调度、`compositions.py` 步骤组合及其 :87 调用形态）、`bohrium_jobs` 表结构、poller 引擎、`trigger_run` 实现、`redis_dao` 现有方法。

---

## 10. 配置（默认值，评审可调）

| 配置 | 默认 | 说明 |
|---|---|---|
| `progress_segments` (N) | 3 | 把一个 invocation 的作业切成 N 段，每攒够 `ceil(total/N)` 个未交付完成做一次进度汇报；progress 唤醒次数据此封顶 |
| `delivery_detail_limit` (M) | 20 | 所有持 snapshot 的 run（含用户轮次）中 `active`/`pending` 各自展开完整详情的条数上界；其余压成溢出摘要（全量 id 仍可见）。无 pending 的会话 snapshot=None，渲染与现状一致 |
| `reservation_ttl` | 60s | NX 原子占位 TTL（仅作同 tick 竞态防护）|
| `scan_limit` | 200 | 单轮 `scan_delivery_units` 返回单元上界（最老 pending 优先；经 owner 过滤后名额全部是可交付单元）|

经环境变量覆盖（如 `BOHRIUM_DELIVERY_PROGRESS_SEGMENTS`、`BOHRIUM_DELIVERY_DETAIL_LIMIT`），沿用 `bohrium_poller._env_int` 模式。

---

## 11. 非目标与已接受限制

**已接受限制（显式决策，不实现自动处理）：**

- **run 级失败不自动重试。** 失败 run 置 `failed` 后该 session 自动交付停摆，至下一次用户交互自愈（任何成功 run 全量 confirm 清积压）。代价显式接受：若 FINAL 那次 delivery run 失败，该批次唯一的 notify 丢失（ops 完成卡片仍以 run_success=False 发出，pull 仍可查）。失败阶梯/熔断式自动重试留 Phase 2。
- **FINAL notify 可能被用户 run 抢先消化**（§7）：信息已在 context 交付，通知不再发。
- **worker crash 残留 `active` 无自动复位**（§6 已知边界三）。
- **owner 不一致残留 pending 行不消化、不 GC**（扫描经 owner 过滤后不进调度、仅占存储；org 切换与脏数据均会产生，§4a）。
- **Redis 故障期间自动交付整体暂停**（NX fail-closed，§6c）：触发链路本就依赖同一 Redis，暂停是背压而非额外损失；恢复后周期 tick 自动续上。瞬断窗口的孤儿 trigger 事件每次故障至多一条（§6 已知边界四）。

**Phase 2 deferrals（量级/能力扩展）：**

- 上万级常态下的 id 分批 delivery：本版守「全量 id 始终可见」，单次 delivery 的 token 底线 = O(单次 pending id 数)。几百~几千可行；上万级需放弃单次全量 id、改 batch（一次只交付/ack 一段 id），触及 ack 语义。
- 任何持久调度态表 + 周期 reconcile（本设计零持久态）。
- delivery_batches / batch_scopes / `delivery_batch_id` 预留-消费-释放（用 worker 内存 `DeliverySnapshot` 替代，不落表）。
- 按时间的 cooldown / 慢批次中途及时性：progress 按完成数量切段，慢批次中途反馈迟钝（pull 仍可查）。
- `bohrium_jobs` 表增长治理（handled 行归档）与两阶段扫描/索引预过滤（§4a）。
- `trigger_run` 副作用顺序根治：trigger 事件写入后置到入队成功之后，或失败补偿删除（§6 已知边界四）。
- owner-aware GC、4 个 policy preset、失败阶梯阈值、heartbeat。
- exactly-once 交付；持久 delivery batch；高通量分页 delivery；跨 run 恢复未 confirm 的 delivery token。
- RUN_END 后 session-local recheck（lean 靠 10s 周期 tick 拾取，first_failure 延迟 ≤ 一个 tick）。

---

## 12. 硬约束（实现须守）

1. **调度器无状态**：唤醒决策仅从 `bohrium_jobs` 当前聚合快照推导，不持久化、不读写任何调度态表，不依赖 `now`。
2. **失败不是无界旁路**：仅首失败有一次性快车道（`failed_handled==0` 推导）；之后失败靠 progress 与 final 覆盖；run 级失败不自动重试（§3）。
3. **非 final 自动唤醒有上界** `≤ 1 + N`（per-invocation，N=`progress_segments`，经 `ceil(total/N)` 阈值 + ack 翻篇保证）；session 非 final 总唤醒 ≤ K×(1+N)，连同 final ≤ K×(2+N)，均与作业数无关。
4. **delivery snapshot 是确认边界 + 全量 id 可见**：DB 层必须读取全量 pending terminal job ids；context 详情可按 `detail_limit` 压缩，但全量 job_id 必须可见，confirm 只能 ack `snapshot.row_ids`。
5. **mark_handled 只发生在 agent 成功消费 snapshot 之后**：poller 不得；trigger enqueued 不得；只有 worker 在 run 成功收尾、**release 之前** confirm。
6. **所有成功 run（不分 origin）confirm 其 snapshot**：渲染范围 = ack 范围；用户 run 与触发 run 同路径。
7. **handled_at = 已纳入一次成功 run 的 delivery snapshot**，非逐 job 深度分析，也非详情一定被展开。
8. **触发前必须过三门**：session identity 一致（SQL owner 过滤为主，门兜竞态窗口）+ DB `status==idle`（active/waiting/failed 均跳过，failed 的停摆为显式接受）+ NX 占位（fail-closed：Redis 不可用时 skip 并计数告警，禁止放行——放行只会产生孤儿 trigger 事件与排队通知，§2/§6c）；不得依赖 `try_acquire`/`session_run_queued` 做跨进程互斥。
9. **交付 at-least-once**：snapshot 边界保证不漏；snapshot 外窗口内完成的 job 留待下轮。
10. **monitor/worker 两条链路不共享运行态**，只经 ledger 协调；纯 DTO/identity helper 可复用。
11. **renderer 升级不改 kernel 决策/编排**：detail_limit 经 `SessionJobs` 字段传递，`from_jobs` 签名与 `compositions.py:87` 调用形态不动；字段为 `None` 时渲染零变化。
12. **作业只能经 session run 内的 ledger port 提交**：invocation 与 run 一一绑定、提交期间 status 门跳过该 session，由此保证 unit 的 `total` 在交付期固定——这是 progress 上界证明与 FINAL 不误报的前提；任何绕过 run 的提交路径都会破坏 §1 的唤醒上界。

---

## 13. 测试（TDD，沿用 `test_bohrium_poller.py` 风格，假对象注入、不依赖真库）

- `tests/services/test_bohrium_completion_scheduler.py`：注入假 jobs_table + 假 sessions_service（控 `get_session`/`get_session_status`）+ 假 redis + 假 stream_service。覆盖：
  - final / first_failure / progress 命中与优先级（含 active==0 时 final 抢占 first_failure）。
  - **progress 阈值 ceil**：`pending >= ceil(total/segments)` 触发；专测 `total=5, segments=3`（step=2，2 次 progress，验证不退化成 4 次）。
  - **progress 上界**：连续 ack 模拟，断言成功 progress 次数 ≤ segments，与 total 取大值无关。
  - first_failure 一次性（`failed_handled>0` 后不命中）。
  - **identity 门（竞态兜底）**：扫描后、触发前 owner 变更时跳过、计 `skipped_identity`。
  - **status 门**：`active`/`waiting` 跳过计 `skipped_busy`；**`failed` 跳过计 `skipped_failed`、不触发**；`skipped_failed > 0` 时 summary 产出 WARN（附 session 清单）。
  - **NX 三态**：`False` → skip；**`None` → 同样 skip、计 `skipped_redis`、tick 级聚合一条 WARN（fail-closed，不触发）**；key 用 `max_pending_terminal_id`；同 session 两单元只发一次。
  - session 级合并：多 eligible 单元一次 trigger_run，按 primary reason；FIRST_FAILURE 时经 §4(d) 取首失败作业信息入 prompt；三种 reason 的 prompt 均含 session 级交付结尾句。
  - invocation_id 为 NULL 的行归入 `''` 哨兵单元，与有值单元互不干扰。
  - `enqueued` 后不写任何持久状态；`busy/error` 不动 ledger。
  - `tick()` 自吞异常返回 `tick_failed=1`。
- `tests/services/test_bohrium_delivery_ack.py`：`snapshot` 返回全量 row/job ids 与行（失败优先序；行字段含 `_AGENT_COLUMNS` 全集，特别断言 `result_dir` 在场）；查询失败或空集合返回 None 不抛；`confirm` 只按 `snapshot.row_ids` 调 `mark_handled_by_ids`；confirm 异常不阻断 release。
- `tests/context/test_session_jobs_source.py`（新增）：`detail_limit` 字段为 None 时与现状逐行一致；为 M 时前 M 条详情 + 溢出摘要，摘要 `job_ids` 与详情行合起来覆盖全量 id；active/pending 各自压缩；M ≥ 总数时无溢出行；同 `job_id` 不同 sandbox 两行不被去重。
- `tests/services/test_bohrium_jobs_wiring.py`：带 snapshot 时 pending 据 snapshot.rows 构造（失败优先、`SessionJobs.detail_limit` 被设置）、**active 仍走实时查询**、不再裸查 `limit=5` 定交付集合；无 snapshot 时读侧不变。
- `tests/worker`：snapshot 在 acquire 后、`run_agent` 前创建并传入，**对用户 origin 的 run 同样生效**；run 成功时 confirm 在 release 前；失败不 confirm；confirm 抛异常仍 release。
- `tests/dao/`（有真库 fixture 才跑）：`scan_delivery_units` 聚合/排序/limit/owner 过滤（owner 不一致行不进单元）；`list_pending_terminal_snapshot` 返回全量 pending（失败优先、无 fixed limit、字段对齐 `_AGENT_COLUMNS`）；`mark_handled_by_ids` 只标 snapshot ids、幂等、分块（>500 ids）可重入；`get_first_pending_failed` 取最早未交付失败。
- `tests/monitor/test_monitor_worker.py`（已存在）：补 scheduler.tick 接入后循环正常、单轮异常不退出。

---

## 14. 端到端时序示例

**一、交互式 3 个 DFT 作业，第 1 个失败（segments=3 → step=ceil(3/3)=1，detail_limit=20 全展开）：**

```
作业1 失败终态 → poller 写 ledger
scheduler.tick: unit(active=2, failed_total=1, failed_handled=0, pending=1) → FIRST_FAILURE
  → identity 过 + status==idle 过 + NX 占位过 → trigger_run(prompt="作业1 失败…", notify=False) → enqueued
worker: run 起点 DeliverySnapshot(row_ids=[job1], job_ids=[job1])
  → port：pending 据 snapshot、active 实时查 → renderer 全展开 job1 → agent 分析
  → 成功：先 confirm(snapshot.row_ids，failed_handled→1) 再 release(idle)
作业2 完成 → unit(active=1, pending=1, failed_handled=1, total=3)
  → first_failure 已交付 → pending(1) >= step(1) → PROGRESS → 触发、确认(ack job2)
作业3 完成 → unit(active=0, pending=1) → FINAL → 触发(notify=True)、确认 → pending=0
```

**二、1000 个作业陆续完成（lean 上界 + 压缩验证，segments=3 → step=ceil(1000/3)=334，detail_limit=20）：**

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

**三、delivery run 失败停摆与用户自愈（失败语义）：**

```
100 作业全部完成 → FINAL 触发 → run 中 LLM 异常 → run_success=False
  → 不 confirm → release(failed)
此后每 tick：status 门见 failed → skipped_failed+1，不触发
  （summary 中 skipped_failed 持续非零 = 该 session 有积压未交付，按 §6b 要求打 WARN；
   该批次 FINAL notify 丢失，属已接受限制，ops 完成卡片仍以 run_success=False 发出）
次日用户发任意消息 → try_acquire 刷回 active → run 成功
  → run 起点 snapshot 含全部 100 pending → context 前 20 详情 + 80 id 溢出
  → 收尾全量 confirm → pending=0，闭环恢复
```

（progress 按完成数量切段，无 budget 计数、无 cooldown、无状态表；详情压缩守住全量 id 可见；让掉的能力——慢批次按时间响应、run 级失败自动重试——见 §11。）
