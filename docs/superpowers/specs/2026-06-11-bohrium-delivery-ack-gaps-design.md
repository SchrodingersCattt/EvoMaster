# Bohrium delivery ack 缺口修复：前台观察集 + STALLED 失速规则

> 日期：2026-06-11。承接 2026-06-09-bohrium-completion-scheduler-design.md 的无状态闭环设计，
> 修复其落地后暴露的两个 handled 标记缺口。范围：worker 侧 ack 边界扩展（缺陷一）与
> monitor 侧失速判定（缺陷二）；不动 poller 状态机、不动 lost 超时、不建新表。

## 1. 缺陷与成因

### 缺陷一：run 内前台查询到的终态永远不会被 ack

`handled_at` 的唯一写入点是 worker 在 run 成功收尾时的 `confirm(snapshot)`
（`src/worker/agent_worker.py`），ack 范围严格等于 run 起点拍下的 snapshot 行。
时序窗口：

1. run 启动时作业仍在运行，不在 snapshot 中（最常见时 snapshot 整体为 None）；
2. run 中途 agent 用 Bohrium 工具 query 到 Finished/Failed，
   `matmaster/tools/builtin/bohrium_tool/tool.py` 调 `record_poll` 把终态写进
   ledger，agent 下载结果并向用户汇报——业务上已处理完毕；
3. run 成功结束，confirm 只 ack snapshot 行，该作业不在其中；
4. 下一个 monitor tick 看到 pending_terminal>0，触发 FINAL/PROGRESS 唤醒，
   agent 被叫起来重复汇总用户已看过的结果。

agent 在单 run 内提交短作业并轮询到完成是最常见模式之一，该模式下每次都多付一个冗余 run。

相关不一致：`src/services/bohrium_jobs_wiring.py` 的 `_RunSessionJobsPort` 在
snapshot 为 None 时走实时查询分支（`query_session_pending_terminal` limit=5），
run 中途新终态的行会进 context 但永远不 ack——违背 ack 范围 = agent 看到范围的
不变量；snapshot 非 None 时 pending 又是冻结的。两分支语义不一致。

### 缺陷二：unknown 卡死作业饿死交付

detail 接口调用失败（持续 5xx、access key 失效、作业被彻底清除后接口报错）时，
`mark_poll_error` 将作业置 unknown。unknown 属活跃态，须连续失联超
`BOHRIUM_POLL_LOST_AFTER_SECONDS`（默认 86400 秒）才转 lost。该窗口内对照
`decide` 三门：FINAL 被 active>0 堵死；unknown 不在失败集合、FIRST_FAILURE
不触发；唯一出口 PROGRESS 要求 pending_terminal ≥ ceil(total/3)。

精确触发条件：N 个作业 k 个失联，已完成数 N−k < ceil(N/3) 时调度彻底静默，
已完成结果躺表 24 小时。例：10 作业 7 失联 3 完成，step=4 > 3，三门全闭。
即使部分结果经 PROGRESS 送出，尾段 pending 不足 step 时同样被卡死的 FINAL
拖到 lost 超时。

**实测事实收窄范围**：欠费删除后平台对 detail 查询返回状态码 -2（Deleted），
是一次成功 poll，`to_ledger_status` 归入 stopped（终态、失败集合），现有链路
正常触发 FIRST_FAILURE/FINAL——欠费场景不落 unknown 限速带。缺陷二的真实残余
仅剩 detail 调用本身失败一类。

## 2. 决策记录（已与用户对齐）

- **看到即处理**：agent 在 run 内通过工具 query 看到终态（工具结果进入对话
  上下文）即认定该作业已交付视野，run 成功收尾时一并 ack。与 snapshot 语义
  对齐——snapshot 路径同样不验证 agent 是否实质总结过。失败的 run 不 ack，
  at-least-once 不变。
- **查询时立即 ack 否决**：run 可能在查询后、汇报前崩溃，ack 已落库则补救唤醒
  丢失，破坏 at-least-once；与 poller、trigger 不得 ack 同一条理由。
- **run 末重拍快照全量 ack 否决**：会把 run 中途由后台 poller 转终态、agent
  从未见过的行误标，直接丢交付。
- **等级 1 边界**：全批失联（pending=0、全 unknown）不建主动唤醒。防重发依赖
  ack 消 pending，全批失联无 pending 可消，去重需引入已通知持久标记，破坏
  无状态闭环；该场景仅剩持续接口失败一种成因，靠 24h lost 兜底 + 用户主动
  询问通道（context 活跃列表始终可见 unknown 作业）。
- **不做 poller 错误分类快速置 lost**：欠费已被 -2 覆盖，残余的彻底清除场景
  走 24h 兜底，YAGNI。
- **纯 pending 年龄规则否决**：不看 unknown 条件的时效规则会把长尾批次
  （作业陆续完成的常态）变成每 T 一醒，炸掉 1+N 唤醒上界。
- **缩短 lost 超时否决**：lost 不可逆且停轮询，平台短时维护恢复后误判即永久
  谎报，24h 保守值不动。

## 3. 设计一：前台观察集与并集 ack（缺陷一）

### 3.1 DeliverySnapshot 形状与 snapshot() 返回语义

`src/services/bohrium_delivery_ack.py`：

```python
@dataclass(frozen=True)
class DeliverySnapshot:
    user_id: str
    org_id: str
    session_id: str
    rows: tuple[dict[str, Any], ...]            # 可为空元组
    detail_limit: int
    observed_terminal: set[tuple[bool, str]]    # run 内前台观察到的终态 (sandbox, job_id)
```

frozen 冻结字段绑定，不妨碍集合自身 add；对象本就是 run 级生命周期，恰好承载
run 内观察记录。

`snapshot(session_id)` 返回语义调整：

- session 身份（user_id、org_id）可解析 → 始终返回 snapshot 对象，rows 允许空；
- 身份解析失败（session 行缺失、org 未绑定、查库异常）→ None，此时既无法 ack
  也无法渲染，行为同现状，未交付行下轮重投；
- rows 查询失败但身份正常 → 空 rows 的 snapshot，本轮不渲染存量 pending，
  观察集照常工作，未渲染行下轮重投。

### 3.2 数据流（一次 run 的时间线）

1. worker acquire 成功后拍 snapshot：rows 即本轮交付边界（可空），
   observed_terminal 空集；
2. snapshot 经 run_agent → `build_bohrium_jobs_ports`，读写两 port 共享引用：
   - 读 port（`_RunSessionJobsPort`）渲染 pending 恒用 snapshot.rows；
   - 写 port（`_BohriumJobLedger`）`record_poll` 在 `to_ledger_status` 判定
     终态时把 (sandbox, job_id) 加入 observed_terminal；`record_kill` 不记
     （kill 请求 ≠ 终态确认，后续确认查询自然落进观察集）；
3. run 成功收尾，confirm ack 并集：先按 snapshot 行 id 调既有
   `mark_handled_by_ids`，再按观察集调新增 `mark_handled_by_job_keys`，
   均空集短路。worker 调用条件（run 成功且 snapshot 非 None）不变。

### 3.3 新 DAO 方法

`src/dao/bohrium_jobs_table.py`：

```sql
UPDATE bohrium_jobs SET handled_at = NOW()
WHERE user_id = %s AND org_id = %s AND session_id = %s
  AND (sandbox, job_id) IN ((...), ...)
  AND terminal_at IS NOT NULL AND handled_at IS NULL
```

- **session_id 约束是安全闸**：`apply_poll` 按 owner+job_id 定位、不带 session，
  会话乙查询会话甲的作业时终态写进甲的行；ack 必须只清本会话行，甲会话应得的
  唤醒一个不少。
- **handled_at IS NULL 保证幂等**：与 snapshot 重叠的行二次更新落空；分块
  （500/批）与 `mark_handled_by_ids` 同构。

### 3.4 渲染分支统一

删除 `_RunSessionJobsPort.load_session_jobs` 的实时查询分支：pending 渲染
变为 snapshot 有则 rows、无则空元组（此时 detail_limit 置 None，空集下无意义）；
active 列表保持每次实时查询（语义是当前还在跑什么，冻结反而与 FIRST_FAILURE
的在跑计数矛盾）。DAO 的
`query_session_pending_terminal` 失去唯一调用方，删除。

统一后的不变量一句话说全：**agent 本轮看到的 pending = snapshot 行 ∪ 前台
查询结果；ack 范围 = 同一集合**。run 中途由后台 poller 转终态、agent 没看过
的行，既不渲染也不 ack，留待下轮——现有冻结语义推广到所有 run。

### 3.5 异常路径与并发

- confirm 抛异常：worker 既有吞异常加告警不变，下轮重投（at-least-once）；
- 并集 ack 中途失败（id 批成功、key 批失败）：幂等谓词保证补投不重复标记，收敛；
- 观察集并发：add 发生在 run 内工具执行，confirm 读取在 asyncio.run 返回后，
  无时间重叠；run 内并行工具同时 add 依赖 CPython 集合操作原子性，足够。

### 3.6 案例

agent 单 run 内提交沙箱短作业 J，轮询三次后 Finished：`record_poll` 写终态、
观察集收 (true, "J")；agent 下载、汇报、run 成功；confirm ack 空 snapshot
（短路）+ 按 key ack J；下一 tick 无 pending，无冗余唤醒。

## 4. 设计二：STALLED 失速规则（缺陷二）

### 4.1 聚合扫描新列

`scan_delivery_units` SQL 增两列，DB NOW() 计算、`decide` 保持纯函数：

```sql
SUM(t.status = 'unknown')                            AS unknown_count,
TIMESTAMPDIFF(SECOND,
    MIN(CASE WHEN t.terminal_at IS NOT NULL
             AND t.handled_at IS NULL
        THEN t.terminal_at END), NOW())              AS oldest_pending_age_seconds
```

HAVING 保证 pending_terminal>0，MIN 必有值、年龄列非 NULL。

### 4.2 判定链

`decide` 在 PROGRESS 之后加一个出口，完整顺序：

1. pending_terminal == 0 → None；
2. active == 0 → FINAL；
3. failed_total>0 且 failed_handled==0 → FIRST_FAILURE；
4. pending_terminal ≥ step → PROGRESS；
5. **新增**：unknown_count == active（剩余活跃全失联）且
   oldest_pending_age_seconds ≥ stalled_after_seconds → STALLED；
6. None。

terminating 态持续失联同样会被 `mark_poll_error` 归一为 unknown（其活跃态
谓词含 terminating），不会卡住全失联判定。

**全部失联而非存在失联，是唤醒上界的关键**：只要还有作业真实运行，批次就在
正常推进，等 step/FINAL 是设计本意；长尾批次绝不会因此每 T 刷屏。失速态下
重复唤醒亦有界：每次唤醒经 ack 清空 pending，再次 STALLED 须等新终态出现且
熟化 T 秒——病态的接口间歇恢复场景至多每作业一次、间隔 ≥T；常态批次维持
1+N 上界不变。

### 4.3 Reason 重排与文案

`Reason` 重排为 PROGRESS=1、STALLED=2、FIRST_FAILURE=3、FINAL=4。数值即
session 合并优先级，STALLED 压过 PROGRESS 取其文案信息量；枚举纯内存、无持久化。
判定顺序 STALLED 在 PROGRESS 之后（单元内出口选择）与枚举优先级（跨单元合并
取舍）不矛盾。

STALLED 文案如实陈述（不沿用仍在运行措辞）：本会话有若干已结束作业的结果
待处理，另有 k 个作业状态长时间无法查询，可能已被平台清理或接口持续异常，
请处理已有结果并检查这些作业。counts 合计补 unknown 字段；结尾照常拼 session
级交付范围说明。被唤醒 agent 拿到全部 pending 行，活跃列表可见 unknown 作业，
可当场逐个查询并向用户报告平台侧报错。

### 4.4 配置

`SchedulerConfig` 加 `stalled_after_seconds`，环境变量
`BOHRIUM_DELIVERY_STALLED_AFTER_SECONDS`，默认 900。

### 4.5 通知策略

不变：仅 FINAL 推送外部通知（`DeliverySpec(notify=...)`）。STALLED 只作为
会话内 agent turn 出现，避免同批作业 STALLED 与 FINAL 双重打扰。

## 5. 已知边界（非目标）

- 全批失联（pending=0、全 unknown）：不主动唤醒，24h lost 兜底 + 用户询问通道；
- 不做 poller 错误分类快速置 lost（404/彻底清除走 24h 兜底）；
- lost 24h 超时不动；
- 跨会话查询的终态行只在其归属会话内交付与 ack（本设计的 session_id 约束
  保证不误吞，归属会话的唤醒照常）。

## 6. 触及文件

- `src/services/bohrium_delivery_ack.py` —— DeliverySnapshot 增 observed_terminal、
  空 rows 语义、confirm 并集 ack；
- `src/services/bohrium_jobs_wiring.py` —— ledger 写观察集；读 port 删实时分支、
  冻结渲染；
- `src/dao/bohrium_jobs_table.py` —— 增 `mark_handled_by_job_keys`；删
  `query_session_pending_terminal`；`scan_delivery_units` 增 unknown_count 与
  oldest_pending_age_seconds；
- `src/services/bohrium_completion_scheduler.py` —— Reason 增 STALLED 并重排、
  decide 增分支、render_prompt 增文案、SchedulerConfig 增 stalled_after_seconds、
  counts 增 unknown；
- `src/worker/agent_worker.py` —— 预期零或近零改动（confirm 调用条件不变）；
- 测试：`tests/services/test_bohrium_completion_scheduler.py`（STALLED 判定四象限：
  全失联超时触发 / 有真实运行不触发 / 未熟化不触发 / 达 step 走 PROGRESS）、
  `tests/services/test_bohrium_delivery_ack.py`（并集 ack、空 snapshot 短路）、
  `tests/services/test_bohrium_jobs_wiring.py`（冻结渲染、观察集写入；删实时
  分支对应用例）、`tests/dao/` DAO 测试组（新 ack 方法谓词、聚合新列）、
  `tests/test_agent_worker_snapshot_confirm.py`（worker 端到端 ack 范围）。

## 7. 验收口径

1. 单 run 内提交并查询到终态的作业，run 成功后不再产生冗余唤醒；
2. 跨会话查询不吞他会话的交付；
3. N 作业 k 失联且 N−k < ceil(N/3) 时，最老 pending 熟化 stalled_after_seconds
   后的下一个调度 tick 触发 STALLED 唤醒，已完成结果送达 agent；
4. 长尾正常批次唤醒次数仍守 1 + progress_segments 上界；
5. 失败 run 不 ack 任何行（at-least-once 不变）。
