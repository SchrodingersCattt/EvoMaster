# Programmatic Agent Run Trigger Design

## Context

当前一次 agent run 只能由用户请求触发。生产链路是单一的：

```
HTTP POST /chat/sessions/{sid}/stream  (src/apis/chat_api.py:200)
  → StreamService.prepare_send_message  (src/services/stream_service.py:442)   写 User/query 事件、生成 task_id、算历史边界
  → StreamService.generate_send_stream  (src/services/stream_service.py:612)   组 job、入队、返回 SSE 流
  → Redis LPUSH "chat:agent_run_queue"  (src/dao/redis_dao.py:255)
  → Worker BLPOP                        (src/worker/agent_worker.py:303)        反查 session owner 当 user_id
  → AgentRunService.run_agent           (src/services/agent_run_service.py:211)
  → Exp.run_stream                      LLM 消息 = [System, *history, User(本轮)]
```

已经存在一个最接近"非 Web 用户触发"的先例：飞书入站（`src/services/feishu_inbound_service.py:122`），它由后台事件回调发起，复用 `prepare_send_message` + `generate_send_stream` 走同一条队列链路，但仍代表一个已绑定的真实用户。除此之外，系统没有任何"由后台进程或外部工具代替用户触发一次 agent run"的通用入口，也没有任何周期性后台任务基础设施（现存定时代码只有 Redis 心跳，不扫库、不触发 run）。

本设计引入一个通用的程序化触发原语：让任何后台进程、外部工具，以及未来的 loop / schedule 驱动器，都能"代替用户注入一条 query 并触发一次 agent run"。注入的这条消息以 `source='System'` 落库供前端区分渲染，但喂给 LLM 时是一条普通 user message。触发产生的 run 复用现有 `/stream` 的全部能力：SSE 事件流、计费、落库。

本设计只做触发原语本身。loop 与 schedule 不在本设计实现，但其接入路线作为原语接口的验收写入文档。

## Goals

- 提供一个通用的"程序化触发一次 agent run"原语，发起方可以是后台进程或跨机器的外部工具。
- 复用现有 `/stream` 全链路：SSE、计费、事件落库，不重接一套执行机制。
- 触发的正确性逻辑（会话确保、并发锁、task 标识、历史边界、入队）与用户发送路径共享同一份内核，不漂移。
- 注入消息以 `source='System'` 落库供前端区分渲染，喂 LLM 时为普通 user message。
- 原语接口预留 `origin` / `dedup_key` / `delivery` / `on_busy` 四个扩展点，并以 loop 和 schedule 两个未来用例验证接口够用。

## Non-Goals

- 不实现 loop 机制（run 结束后自循环），仅写接入路线。
- 不实现 schedule 机制（周期/定时调度器、调度表、常驻调度进程），仅写接入路线。
- 不实现 HPC 作业完成检测（Bohrium poller 是另一份独立设计，见 `2026-06-01-bohrium-job-ledger-design.md`）。本原语不绑定任何具体触发源。
- 不在第一版实现 `on_busy` 的 `requeue` / `preempt` 策略，仅保留枚举位。
- 不引入跨步骤回滚：入队失败时已写的事件不回滚，沿用现有用户发送路径的既有语义。
- 不为系统触发新增独立 HTTP 端点；复用并泛化现有 `/stream`。

## Architecture

四层，新增的只有"入口层的内部发起路径"与"服务层的系统触发适配器"。执行层完全复用。

```
入口层    POST /chat/sessions/{sid}/stream  （泛化：用户发起路径 + 内部发起路径）
          内部发起：X-Internal-Token 鉴权、user_id 取 session owner
              │
服务层    ┌─ 用户发送适配器：prepare_send_message / generate_send_stream（改造为调内核）
          └─ 系统触发适配器：trigger_run（本设计主产物）
              │  两个适配器只差「写什么事件」与「身份/扩展点来源」
          _prepare_run（共享内核）
          ensure_session → 并发锁 → task_id/invocation_id → pre_turn_history_event_id
          → event_writer(写本轮发起事件) → 组 job → set waiting → lpush
              │
执行层    Redis 队列 → Worker BLPOP → run_agent → Exp.run_stream   ← 完全不改
```

进程内的驱动器（未来 loop / schedule）直接调服务层的 `trigger_run`，不绕 HTTP；跨机器的外部工具走泛化后的 `/stream` 内部发起路径。两条入口最终都汇入同一个 `trigger_run` → `_prepare_run`。

## Components

### `_prepare_run`（共享内核）

把"怎么正确触发一次 run"从 `prepare_send_message`（src/services/stream_service.py:442）和 `generate_send_stream` 的入队段（src/services/stream_service.py:704-751）抽取出来，收敛为唯一一处。示意签名：

```python
def _prepare_run(
    self,
    session_id: str,
    *,
    user_text: str,
    files: list[str] | None,
    images: list[str] | None,
    workspace_paths: list[str] | None,
    event_writer: Callable[[str, str], None],   # 入参 (task_id, invocation_id)，写本轮发起事件
    id_prefix: str,                             # 'sse_' | 'ws_' | 'trig_'
    mode: str,
    llm: str | None = None,
    model: str | None = None,
    byok_credential_id: str | None = None,
    bohrium_required: bool = False,
    remote_workdir: str | None = None,
    on_busy: str = "skip",
) -> RunHandle | Busy:
    ...
```

职责，严格按此顺序：

1. `ensure_session`（src/services/sessions_service.py:161），但要求 session 已有 owner（见 Invariants）。
2. `try_acquire_session_run`；失败按 `on_busy` 返回 `Busy`。
3. 生成 `task_id = id_prefix + uuid16`、`invocation_id = 'inv_' + uuid16`。
4. `set_session_last_task` / `record_session_version`。
5. `pre_turn_history_event_id = _get_pre_turn_history_event_id(sid)`，并据此构造 `TurnInput`。**必须在第 6 步写事件之前取**。
6. `event_writer(task_id, invocation_id)`：写本轮发起事件（User/query 或 System/trigger）。
7. 组 job → `set_session_status('waiting')` → `lpush_agent_run_job`。
8. 返回 `RunHandle(task_id, invocation_id, turn_input, ...)`。

`event_writer` 是策略点：用户发送传 User-event writer，系统触发传 System-event writer。历史边界、并发锁、入队这些最容易出 bug 的逻辑只有一份。

用户发送路径改造后：`prepare_send_message` 解析 `ChatSendRequest`、处理 `replace_last_turn` / bohrium / llm-model-byok 后调 `_prepare_run`（传 User-event writer、`id_prefix='sse_'`），随后 `generate_send_stream` 接着做 SSE 订阅回放。系统触发路径不做 SSE 订阅回放（事件由 worker publish 到 channel，在线前端订阅流自取）。

### 系统触发适配器 `trigger_run`

本设计主产物。示意签名：

```python
def trigger_run(
    self,
    session_id: str,
    prompt: str,
    *,
    origin: str,                       # 'hpc_job' | 'cron' | 'loop' | 'external_tool' | ...
    dedup_key: str | None = None,
    delivery: DeliverySpec | None = None,
    on_busy: str = "skip",
    mode: str | None = None,
    llm: str | None = None,
    model: str | None = None,
) -> TriggerResult:
    ...
```

职责：

1. 校验 session 已存在且有 owner，否则返回 error（不静默创建无主 session）。
2. dedup 预检：若 `dedup_key` 已在 Redis 存在 → 返回 `deduped`，不触发。
3. 构造 System-event writer：写 `source='System'`、`type='trigger'`、`content={'text': prompt, 'origin': origin}`。
4. 调 `_prepare_run(user_text=prompt, event_writer=System-writer, id_prefix='trig_', on_busy=on_busy, ...)`。
5. 若返回 `Busy` → 返回 `busy`，**不标记 dedup_key**（使该触发可被重试）。
6. 成功入队后，若有 `dedup_key`，`SET dedup_key <task_id> NX EX <ttl>` 标记。
7. 把 `delivery` 写进 job payload，供 worker 完成时决定通知。
8. 返回 `TriggerResult(status='enqueued', task_id, invocation_id)`。

### 泛化 `/stream` 入口

在 `chat_stream`（src/apis/chat_api.py:200）增加一条内部发起路径，与用户发起路径并存：

- **鉴权分叉**：带合法 `X-Internal-Token`（环境变量配置的共享密钥、仅内网可达）时，进入内部发起模式，跳过 `can_access_session`（src/apis/chat_api.py:246）的用户登录鉴权；否则走原用户登录鉴权。
- **身份**：内部发起时 `user_id = get_session_user_id(sid)`（session owner），`origin` 取自请求体。
- **额度**：内部发起时 `check_quota_status`（src/apis/chat_api.py:267）以 session owner 为主体。
- **分派**：内部发起调 `trigger_run`；用户发起调 `prepare_send_message`。两者底层都过 `_prepare_run`。
- **返回**：照常 `StreamingResponse(generate_send_stream(...))`（src/apis/chat_api.py:363）。事件流由 worker publish 到 `chat:stream:{sid}` channel，发起方与在线前端订阅流均可消费。

内部发起请求体在 `ChatSendRequest`（src/models/chat.py:365）基础上扩展可选字段：`origin`、`dedup_key`、`on_busy`、`delivery`。这些字段仅内部发起模式有意义。

### System 触发事件与历史还原

- **落库**：`source='System'`、`type='trigger'`、`content={'text': prompt, 'origin': origin}`，带 `task_id` / `invocation_id`，经 `add_event`（src/dao/chat_events_table.py:485）写入 `evo_chat_events`。`'System'` 已是合法 source 值（如 src/services/stream_service.py:756 的错误事件）。
- **前端渲染**：前端据 `source='System'` / `type='trigger'` 渲染成系统触发样式。后端只保证可区分，前端渲染本设计不实现。
- **历史还原**：在 `events_to_dialog_messages`（src/services/chat_history.py:400）的 `source=='User' and typ=='query'` 分支（src/services/chat_history.py:442）旁，新增 `source=='System' and typ=='trigger'` 分支，做与 User/query 完全相同的轮次边界重置（flush pending reasoning / tool calls、清 turn 状态），再 `out.append(UserMessage(content=<text>).model_dump())`。喂给 LLM 的就是一条普通 user message。

第一版 LLM 侧文本即 `prompt` 原文，不加系统来源前缀；来源信息由 `origin` 字段承载（用于前端渲染、审计、dedup），不注入 LLM 文本。调用方若希望 LLM 感知来源，自行在 `prompt` 文本里表达。

## Extension Points

四个扩展点定在原语接口，每个都由 loop / schedule 这两个未来用例反向验证其必要性。

### origin

来源标记，取值如 `hpc_job` / `cron` / `loop` / `external_tool`，第一版为开放字符串、约定常用值。写进 System 事件的 content，供前端渲染、审计、以及 dedup_key 命名。必填。

### dedup_key

幂等键。去重用 Redis `SET dedup_key <task_id> NX EX <ttl>` 实现：

- 触发前预检，命中已存在 → 返回 `deduped`，不触发。
- **仅在成功入队后才标记**。`busy` / `deduped` / error 路径都不标记，使被跳过的触发可重试。
- `ttl` 控制去重窗口，可由调用方传入，给默认值（如 24h）。
- 可空，不传则不去重。

选 Redis 而非数据库唯一键：与现有队列 / 心跳基础设施一致，TTL 自然过期，轻量。周期与循环机制最容易重复触发（schedule 重投、loop 抖动、at-least-once 队列重复），dedup_key 在原语层兜住，各驱动器无需自己防重。

### delivery

控制 run 完成后的通知。worker 当前在 run 结束时无条件发飞书完成 / 失败卡片 + 邮件（src/worker/agent_worker.py:446-559）；本设计让 worker 读 job payload 里的 `delivery` 决定是否发、发到哪。第一版 `delivery` 最小形状为 `{notify: bool}`（是否发完成通知），后续可扩展通知渠道等字段；缺省时取按 `origin` 约定的默认值。

不同 `origin` 通知需求不同：HPC 作业完成应通知；未来 loop 自循环每轮都通知会刷屏。因此通知是按场景可调的参数，由调用方按 `origin` 定，给合理默认。落库始终发生（用户下次打开会话能看到完整结果），SSE 是用户在线时的实时增强，`delivery` 通知是用户离线时唯一的主动告知通道。

### on_busy

会话运行锁（`try_acquire_session_run`）被占（用户正在对话）时的策略：

- `skip`（第一版默认）：放弃本次触发，返回结构化 `busy`，由调用方重试。配合 dedup 仅成功后标记，重试安全。
- `requeue`：系统侧延迟重投。第一版不实现，仅保留枚举位（需要延迟队列基础设施）。
- `preempt`：取消当前 run 强插。不实现（打断用户、语义危险）。

## Data Flow

以 HPC 作业完成触发为例（外部进程发起）：

```
外部进程（检测到作业 123 完成）
 → POST /chat/sessions/{sid}/stream
      Header: X-Internal-Token: <token>
      Body: {content:"作业123已完成，请下载并分析结果", origin:"hpc_job", dedup_key:"job:123:done", delivery:{notify:true}}
 → chat_stream 内部发起分支：
      校验 X-Internal-Token
      user_id = get_session_user_id(sid)           # session owner
      check_quota_status(owner)
      → trigger_run(sid, prompt, origin='hpc_job', dedup_key='job:123:done', delivery=..., on_busy='skip'):
          session 有 owner 校验
          Redis EXISTS "job:123:done"? 命中 → 返回 deduped（幂等，不触发）
          未命中 → System-event writer 准备好
          _prepare_run(user_text=prompt, event_writer=System-writer, id_prefix='trig_', on_busy='skip'):
              ensure_session（owner 已在）
              try_acquire_session_run → 被占则返回 Busy（on_busy=skip → trigger_run 返回 busy，不标记 dedup）
              task_id='trig_xxx' / invocation_id='inv_xxx'
              pre_turn_history_event_id = 当前最大 event id    # 写事件【之前】取
              写 System/trigger 事件（content={text, origin}）
              组 job（含 turn_input、delivery、origin）→ set waiting → lpush
          成功 → SET "job:123:done" NX EX 86400
          返回 TriggerResult(enqueued, task_id)
 → StreamingResponse(generate_send_stream)  # 发起方可消费可不消费
 ━━ 进程边界 ━━
 Worker BLPOP → user_id = get_session_user_id(sid)（owner）→ run_agent
 → Exp.run_stream:
     restore_history → events_to_dialog_messages 把 System/trigger 还原成 UserMessage
     LLM 消息 = [System, *history, User(本轮=prompt)]
     run 中事件 publish 到 chat:stream:{sid}（在线前端订阅流实时收到）
 → run 结束 → 计费算 owner → 按 job.delivery 决定是否发飞书/邮件通知
```

## Invariants & Error Handling

- **session 必须已存在且有 owner**：内部触发的 `user_id` 取 session owner，计费、历史、鉴权都依赖它。`trigger_run` 在 session 不存在或无 owner 时返回 error，**绝不静默创建无主 session**，否则计费无主、鉴权失效。
- **历史边界单调**：`pre_turn_history_event_id` 必须在写 System 事件之前取，否则注入的这条会被算进"历史"，使 LLM 看到重复消息。这条不变量与用户发送路径（src/services/stream_service.py:549 在 :557 写事件之前取）一致。
- **dedup 标记时机**：仅成功入队后标记 `dedup_key`。`busy` / error 路径不标记，保证被跳过的触发可重试；`deduped` 路径本就因已标记而短路。
- **入队失败留孤儿事件**：现有用户路径就是先写发起事件（src/services/stream_service.py:557）再入队（src/services/stream_service.py:751），入队失败时已写事件不回滚（src/services/stream_service.py:751-771 仅 set idle + 删 queued 标记 + 回错误事件）。系统触发沿用同一既有语义，第一版不引入跨步骤回滚。
- **鉴权失败**：缺失或错误的 `X-Internal-Token` → 拒绝内部发起。
- **占锁**：`try_acquire_session_run` 失败 → 按 `on_busy`（第一版 `skip` 返回 `busy`）。

## Billing & Auth

- **计费主体**：内部发起的 run 计费算在 session owner 头上。worker 本就反查 session owner 当 `user_id`（src/worker/agent_worker.py:348），`run_agent` 用 `BillingLLMProvider` 计费，无需额外改动即归属 owner。
- **额度检查**：内部发起时 `check_quota_status` 以 session owner 为主体。
- **鉴权边界**：用户发起走 `can_access_session(X-User-Id)` 的用户登录鉴权；内部发起走 `X-Internal-Token` 的可信内部系统鉴权，仅内网可达。两条鉴权在 `chat_stream` 入口分叉，互不削弱。

## Delivery

run 完成后的结果送达分三层：

- **落库**：始终发生，事件落 `evo_chat_events`，用户下次打开会话看到完整结果。这是兜底。
- **SSE**：worker 把 run 事件 publish 到 `chat:stream:{sid}` channel（src/services/stream_service.py 的 publish 路径），用户在线时前端订阅流实时收到。发起方也可消费 `/stream` 返回流。SSE 仅在持有连接者在场时有效。
- **通知**：用户离线时唯一的主动告知通道。worker 完成时按 job payload 的 `delivery` 决定是否发飞书 / 邮件（复用 src/worker/agent_worker.py:446-559 的现有完成通知逻辑）。

## Extensibility: loop / schedule 接入路线

两者都是"何时触发"的薄驱动器，复用同一个 `trigger_run`，不改原语。这是原语接口的验收。

### loop（run 结束后自循环）

注册一个 `RUN_END` hook handler（`HookEvent.RUN_END`，matmaster/core/hooks.py:35；emit 点 matmaster/core/agent.py:192，当前是空转扩展点）：

```python
async def on_run_end(ctx):
    st = loop_state.get(ctx.session_id)
    if st and st.should_continue(ctx):              # 未达成目标 / 仍在预算内
        trigger_run(ctx.session_id, st.next_prompt,
                    origin="loop", dedup_key=f"loop:{st.id}:{st.turn}",
                    delivery=st.delivery, on_busy="skip")
hook_executor.on(HookEvent.RUN_END, on_run_end)
```

loop 的增量只是 loop 状态（剩余轮数 / 终止条件 / 预算）+ 一个 hook handler。

### schedule（周期 / 定时）

一张 schedule 表（`session_id`、`prompt`、`interval`/`cron`、`next_fire_at`、`origin`、`delivery`）+ 一个常驻扫表循环，抄即将落地的 Bohrium poller 的"扫 `next_*_at <= NOW()` + `FOR UPDATE SKIP LOCKED` 抢批 + 推进时间"范式（见 `2026-06-01-bohrium-job-ledger-design.md` 的 Background poller 节）：

```python
def schedule_tick():
    for row in claim_due_schedules():               # 扫 next_fire_at<=now，FOR UPDATE SKIP LOCKED
        trigger_run(row.session_id, row.prompt,
                    origin="cron", dedup_key=f"sched:{row.id}:{row.fire_epoch}",
                    delivery=row.delivery, on_busy="skip")
        advance_next_fire_at(row)
```

schedule 的增量是一张表 + 复用 poller 扫表范式 + 调原语。"怎么触发"完全不重写。

## Rationale

**为什么泛化 `/stream` 而非新增独立端点。** 系统触发需要 SSE、计费、前端渲染区分三样能力，而这三样都是 `/stream` 现成的：SSE 由 worker publish 到 channel + `generate_send_stream` 订阅（src/services/stream_service.py:642-691）天然提供；计费由 worker 反查 owner + `BillingLLMProvider` 提供；渲染只需事件落库可区分。独立端点等于把这三样重新接一遍。代价是 `/stream` 入口要按发起身份分叉鉴权（用户登录 vs 内部 token），并让额度检查、事件 source 按内部发起分支——这是可控的局部条件，换来不重复三套现成能力。

**为什么共享内核而非并行实现。** `trigger_run` 与用户发送在"怎么正确触发一次 run"上本就是同一件事（会话确保、并发锁、task 标识、历史边界、入队），只有"写什么事件"和"身份 / 扩展点来源"不同。把公共部分抽成 `_prepare_run`、用 `event_writer` 策略区分，是复用最大化、重复最小化的选择。两份相似的触发逻辑会在历史边界 / 并发 / 入队这些最不该重复的地方漂移。

**为什么 System 只体现在前端渲染、LLM 侧是 user message。** LLM 协议中途无"系统中插"角色，对话中的系统通知最终都以 user role 承载。把 `source='System'` 的作用域限定在前端渲染 + 审计，LLM 侧还原成普通 `UserMessage`，避免在历史还原 / 消息构造里引入特殊角色处理；`origin` 字段已足够支撑前端区分与审计。第一版不往 LLM 文本注入来源前缀，遵循 YAGNI，需要时调用方可在 prompt 自行表达。

**为什么先做原语、不内建 loop / schedule。** loop 与 schedule 的差异只在"何时触发"，共享同一套"怎么触发"。先把"怎么触发"这块硬逻辑收敛成正交原语，loop / schedule 就是其上的薄驱动器。它们的策略细节（cron 语法、loop 终止条件与预算）尚未敲定，过早实现会绑死接口。原语接口以这两个用例反向验证（四个扩展点 + 两段接入路线），保证不返工。

**为什么 dedup 用 Redis、且仅成功后标记。** Redis `SET NX EX` 与现有基础设施一致、TTL 自然过期、轻量。仅成功入队后标记，使 `busy` / error 被跳过的触发可重试，避免"想触发却被锁挡住、又被 dedup 永久挡住"的死角。

**为什么 `on_busy` 第一版只做 `skip`。** `skip` 不需要任何新基础设施，配合 dedup 重试语义已能覆盖"用户正在聊则稍后重试"。`requeue` 需要延迟队列、`preempt` 会打断用户，二者都是更大的承诺，留枚举位按需再加。

## Testing Plan

- **共享内核**：用户发送与系统触发两个适配器复用同一 `_prepare_run`；历史边界正确——注入消息不被算进自身这一轮的历史。
- **System 事件落库与还原**：落库 `source='System'`、`type='trigger'`、content 带 `origin`；`events_to_dialog_messages` 把它还原成 `UserMessage`，轮次边界重置与 User/query 一致（flush pending、清 turn 状态）。
- **dedup**：同 `dedup_key` 第二次触发返回 `deduped`；`busy` / error 路径不标记 key，可重试；`ttl` 到期后可再次触发。
- **鉴权**：缺失 / 错误 `X-Internal-Token` 拒绝内部发起；合法 token 跳过用户登录鉴权、`user_id` 取 session owner。
- **计费**：内部发起的 run 计费归属 session owner；额度检查以 owner 为主体。
- **约束**：session 不存在或无 owner 时 `trigger_run` 报错，不创建无主 session。
- **on_busy**：会话运行锁被占时 `skip` 返回 `busy` 且不标记 dedup。
- **SSE**：内部发起的 run 事件 publish 到 `chat:stream:{sid}`，订阅流能收到。
- **通知**：job payload 的 `delivery` 控制 worker 完成通知的开关与去向。
- **入队失败语义**：入队失败时已写的 System 事件不回滚，与用户路径行为一致。
- **接入路线（接口验收，可用桩验证签名兼容）**：loop 的 `RUN_END` handler 与 schedule 的扫表循环都能仅通过调 `trigger_run`（不改其签名）完成触发。
