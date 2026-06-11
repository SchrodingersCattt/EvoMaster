# Bohrium 作业完成调度器 — 设计讨论纪要

- 状态：讨论中（未定稿，不含实现方案）
- 日期：2026-06-05
- 分支：feat/bohrium_job
- 定位：本文是 Bohrium 闭环中「作业完成调度器」子设计的讨论纪要，记录讨论脉络、已达成共识、硬约束与待设计维度，作为后续正式 design spec 的输入。

## 0. TL;DR

- Bohrium 闭环的两端都已落地：作业台账写入（BohriumTool 写 ledger）、poller 轮询核心（BohriumJobPoller.run_once）、agent run 触发原语（trigger_run）。但中间接线断了。
- 把这条接线天真地做成「每个作业完成唤醒一次 agent run」会在大批量场景爆炸：1000 个作业约等于 1000 次 LLM run，上下文与 token 成本不可控。
- 核心结论：唤醒 agent run 的次数必须与作业完成数解耦。这需要一个独立的、带背压的批量消费调度器。本文界定这个调度器、列出待设计维度，留待逐维度定稿后再写正式 design spec 与实现 plan。

## 1. 背景：闭环缺什么

近两天 feat/bohrium_job 分支上有三条独立但指向同一目标的主线已落地：

- Bohrium Job Ledger：一张 bohrium_jobs 台账表 + DAO + Port + BohriumTool 注入写入 + poller 核心类。Phase 0~4 代码完成。
- Agent Run Triggers：通用触发原语 trigger_run，以 System/trigger 落库、喂 LLM 时还原成 UserMessage，带 origin / dedup_key / delivery / on_busy 扩展点。原语本身完成。
- Dynamic Context Limit：与本议题解耦，已闭合，不在本文范围。

这三条线其实服务于同一个端到端能力：让 Bohrium 异步作业完成后，自动唤醒 agent 继续工作（读结果、分析、回复用户）。但两端之间的接线尚未做：

```
Bohrium 作业提交 ──写入──> [Ledger 台账]      ← 已建好
                              │
                     poller 后台轮询          ← 核心类建好，但没人调度
                              │
                    检测到 job 完成(终态)
                              │
                    ✗ 断点：没有接线
                              │
                  [trigger_run 注入 System 消息] ← 原语建好，但没有调用方
                              │
                       唤醒 agent 继续
                              │
                  agent 交付后 mark_handled 出队 ← 没有触发方
```

三个具体缺口：

1. poller 的 run_once 全仓无调用方（bohrium_poller.py:51），后台不会自动推进非终态作业。
2. 终态作业不会回灌 trigger_run（poller 与触发原语零联动）。
3. mark_handled 无触发方（ports.py:149），pending_terminal 待交付队列永不出队。

## 2. 讨论脉络：从天真模型到独立调度器

本节记录设计认识的三次迭代，这些修正主要由用户推动。

### 2.1 v0 天真模型

最初提出的闭环模型：

- poller 只把作业推进到终态；终态且未交付的作业进入该 session 的待交付队列（已实现的 query_session_pending_terminal）。
- 唤醒来源有二：① poller 在作业刚转终态那一轮对该 session 触发一次 run；② 任意 run 结束时（RUN_END hook）若待交付队列非空，再兜底触发一次。
- 一次 run 处理待交付队列里全部作业，处理完 mark_handled 出队。
- 依赖 session 运行锁把并发完成的作业串行化、合并。

### 2.2 修正一：必须分场景

用户指出 v0 模型把不同情况糅在一起，无法回答。需要按作业状态组合精确定义行为：单个作业完成、N 个完成但未全完成、作业失败，三种情况行为不同。由此产出了第 6 节的行为基线。

### 2.3 修正二（关键）：规模解耦

用户指出 v0 模型的根本盲点：HPC 场景下用户可能大批量提交（例如 1000 个 job，每约 10 分钟完成一个）。天真模型让 run 次数等于作业完成数，会导致约 1000 次 agent run，上下文与 token 爆炸。运行锁的合并在这里救不了——作业稀疏完成时 session 早已空闲，每个都会独立触发。

由此确立核心原则：唤醒 agent run 的次数必须与作业完成数解耦。

### 2.4 结论

用户进一步指出：这本质上需要详细设计一个队列或调度策略，且该策略需要单独详细讨论。因此把「作业完成调度器」从整个闭环里拆出来，作为核心子设计单独立项，本文即其讨论纪要。

## 3. 已达成的共识

- 核心原则：唤醒 agent run 的次数与作业完成数解耦。
- 成本分层：poller 刷新台账状态是廉价操作（一条 SQL UPDATE，每个作业每轮 poll 都做无所谓）；唤醒一次 agent run 是昂贵操作（组装上下文 + 调 LLM + 烧 token，次数必须严格受控）。
- 本质权衡：反馈及时性与 token 成本互相拉扯，且最优点因任务而异（3 个关键 DFT 构型 vs 1000 个高通量筛选作业，答案完全不同）。因此触发策略应做成可配置（按提交批次或 origin 声明），而非全局写死一个值。
- 结构分层：整个闭环分两层。外层是机械接线（poller 怎么周期跑、trigger_run 怎么调、mark_handled 谁标记、context 怎么渲染），相对确定且依赖内层；内层是这个作业完成调度器，是真正要打磨的核心。先设计内层，外层顺着接。

## 4. 三条硬约束（来自代码，是设计地基）

1. 终态作业天然只产生一次完成信号。终态作业的 next_poll_at 置 NULL（apply_poll 的 CASE，bohrium_jobs_table.py:113-118），而 poller 只 claim next_poll_at <= NOW() 的作业（claim_due_batch / _select_due_for_update，bohrium_jobs_table.py:227）。因此一个作业从活跃转终态，poller 只在那一次 poll 检测到，之后永不再碰它。调度器的输入是一个已去重的完成事件流。
2. session 运行锁是天然串行边界与背压点。一个 session 同时只能有一个 run（try_acquire_session_run，stream_service.py:329；占锁失败 trigger_run 返回 busy）。
3. 已有待交付队列可复用为缓冲。query_session_pending_terminal 返回 terminal_at IS NOT NULL AND handled_at IS NULL 的作业（bohrium_jobs_table.py:207-221）并渲染进 agent context（SessionJobs.pending_terminal_jobs，ports.py:98-105）；mark_handled 幂等出队（bohrium_jobs_table.py:161）。调度器不必另造队列。

## 5. 调度器问题定义

一句话：它是一个带背压的批量消费调度器，夹在两个频率天差地别的东西之间。

- 输入：一个可能极高频的作业终态事件流（分钟级陆续完成，规模可达千级）。
- 输出：一个严格受控频率的 agent run 唤醒流（次数与作业数解耦）。
- 职责：在两者之间做聚合 + 节流 + 调度，可配置地平衡反馈及时性与 token 成本。

## 6. 行为基线（小批量场景，作为参考起点）

以下是小批量场景下的预期行为。注意这是基线，大批量必须在其上叠加第 7 节的节流策略。

- 多个作业、只有 1 个完成、其余在跑：完成的进待交付队列；agent 被唤醒时 context 看到完整视图（A 已完成 + B、C 仍在运行），处理 A 后 mark_handled(A)。不等其余作业，单个完成即继续。
- 完成 N 个、仍有未完成：N 个完成的都进队列；session 空闲时先后触发被运行锁串行化，忙时堆队列由 RUN_END 兜底一次性消费；仍在跑的不触发。净效果：完成多少处理多少，并发完成的合并成尽量少的 run。
- 作业失败：failed / stopped 也是终态，同样进队列、同样唤醒 agent；区别只在注入消息标明失败状态。是否自动重试是单独决策（见第 7 节维度 2 与第 9 节未决问题）。

## 7. 待详细设计的维度（设计空间）

每个维度给出候选与初步倾向，倾向均未经最终确认。

1. 聚合作用域：按什么把作业归为一个唤醒单元。
   - 候选：session / invocation（同一次提交动作的一批，台账有 invocation_id 字段）/ spawn（subagent 维度，台账有 spawn_id 字段，但当前恒为 None）。
   - 说明：唤醒 run 本身是 session 级（trigger_run 对 session），但判定「一批是否该处理」可能用 invocation 级语义。两者可分离。

2. 触发条件：一批在什么条件下值得唤醒一次 run。
   - 候选：数量阈值（攒够 N 个完成）/ 时间节流（距上次唤醒超过 T）/ 整批收尾（该作用域活跃作业清零）/ 异常驱动（失败或失败率超阈值）。
   - 待定：选哪些、如何组合（先到先触发 / 与逻辑）、优先级。
   - 初步倾向：以数量阈值或整批收尾为主轴控成本（时间节流在低速完成时省不了多少，见第 8 节陷阱 2）；失败默认也唤醒但不自动重试，重试由 agent 在用户确认后做。

3. 调度状态与执行体：状态放哪、谁驱动判断。
   - 状态候选：纯扫 bohrium_jobs 表实时计算（无额外状态）/ redis 维护每作用域的计数与时间戳 / 单独一张调度表。
   - 执行体候选：poller 每轮 run_once 顺带做 / 独立调度循环 / 依赖 RUN_END hook 做被动兜底。
   - 关联缺口：poller 调度入口本身也还没有（run_once 无调用方），二者可一并设计。

4. 唤醒载荷与处理上限：唤醒时给 agent 什么、一次处理多少。
   - 候选：聚合视图（成功/失败计数 + 失败明细 + 上限 M 条详情）而非逐作业全文；单次 run 最多处理 M 个作业；超过 M 的分批到后续 run。
   - 初步倾向：用聚合摘要，设单次上限 M，避免单次 run 上下文爆炸。

5. 并发幂等与硬背压：防重复、防失控、容错。
   - 候选：多 poller 实例下同一作用域不被重复唤醒（dedup_key 设计）；任何配置下的唤醒频率硬上限（防爆炸的最后一道闸，独立于可配置策略）；唤醒 / run 失败的退避与重试。
   - 初步倾向：强烈建议设硬频率上限，作为所有可配置策略之上的兜底闸。

6. 可配置性：参数从哪来。
   - 候选：N / T / M 等参数按提交批次声明（提交大批量时携带策略）/ 按 origin 约定默认 / 全局默认值。
   - 待定：配置存储位置（job 表字段 / run 参数 / 配置文件）。

## 8. 已识别的设计陷阱（讨论中浮现，后续设计须规避）

1. mark_handled 不能早于 agent 消费。若 poller 触发后立即 mark_handled，agent run 走到 context assembly 查 pending_terminal 时作业已出队，agent 反而看不到。mark_handled 必须发生在 agent run 真正读取 / 消费待交付作业之后（例如 RUN_END，或消费确认后），不能由 poller 在触发那一刻代劳。
2. 低速完成下时间节流救不了成本。当作业完成速率（分钟级）低于或接近节流时间窗口时，时间阈值形同虚设。真正省 token 要靠数量攒批或整批收尾，代价是延迟。
3. 运行锁合并只在密集完成时有效。作业稀疏完成时 session 已空闲，运行锁合并不了，会退化成逐个触发。不能把运行锁当作主要的节流手段。

## 9. 下一步与未决问题

下一步：从维度 1（聚合作用域）+ 维度 2（触发条件）切入深入，它俩定了调度器骨架就出来，3 到 6 是把骨架落地。逐维度定稿后，转写正式 design spec，再写实现 plan。

未决问题（待用户确认）：

- 聚合作用域到底取 session 还是 invocation，subagent 提交的作业（spawn_id 当前恒为 None）如何归属。
- 触发条件的主轴与组合、失败作业的默认处理（只通知 / 自动诊断 / 自动重试）。
- 是否需要跨 session 的全局并发上限与公平性（避免单用户大批量饿死其他会话的唤醒）。
- 可配置策略的承载方式（提交批次如何声明策略）。

## 附：关键代码锚点

- poller：bohrium_poller.py:51（run_once，无调用方）、:67（_poll_one）、:19（compute_poll_backoff，30→600 退避）。
- 台账 DAO：bohrium_jobs_table.py:236（claim_due_batch，FOR UPDATE SKIP LOCKED）、:90（apply_poll，终态单调 + next_poll_at 置 NULL）、:161（mark_handled）、:207（query_session_pending_terminal）、:193（query_session_active）。
- 触发原语：stream_service.py:419（trigger_run）、:300（_prepare_run 共享内核）、:329（try_acquire_session_run）、:406（_enqueue_run）、:179（TriggerResult，状态 enqueued/deduped/busy/error）、:443（dedup_key_exists）。
- 上下文渲染：ports.py:98-105（SessionJobs.active_jobs + pending_terminal_jobs）、:149（BohriumJobLedgerPort.mark_handled，无触发方）。
- RUN_END hook：分支里已有 RUN_END 事件作为 loop / 兜底接入点（位置待实现时核实），是维度 3 被动驱动与陷阱 1 的关键挂点。
