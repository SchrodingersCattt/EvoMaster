# AskQuestion 迁移到通用 per-request 交互传输底座 设计

> 日期：2026-06-18
> 状态：设计稿（已吸收一轮外部 review），待 writing-plans 拆分实现计划。
> 范围：把现有 AskQuestion 的"发起交互 + 等待用户回复"链路，从 **session 级共享 reply list** 迁移到一套**通用 per-request 交互传输底座**；AskQuestion 是该底座的第一个使用者。对外契约统一成通用 `interaction_*` 信封 + `interactions/{request_id}/reply` endpoint，前端 `scimaster-bohr-chat` 同步迁移。**不实现** Bohrium submit review，但底座为其预留接入位。

## 1. 背景与动机

当前 AskQuestion 的回复通道是一条 **per-session 共享 Redis list**（`chat:confirmation_reply:{session_id}`）。它现在能正常工作，仅仅因为两个隐含前提同时成立：

- session 级一次只有一个活跃 run（`try_acquire_session_run`）；
- 系统里只有 AskQuestion 一种交互，且它有进程内互斥（工具 `interaction` 独占锁 + bridge `asyncio.Lock`）。

一旦未来引入第二种交互（Bohrium submit review）并共用这条 list，会出现**错误消费**：worker 端取回复时若 `request_id` 不匹配，现有逻辑直接 `raise` 且不把消息放回（`interaction_bridge.py:67-72`），导致该回复被永久吃掉、真正的等待者超时。典型触发场景是**迟到回复**——上一个已超时交互的回复姗姗来迟，被另一个交互的 `BLPOP` 抢到。

本设计把传输层升级为 **per-request key + pending registry + active 索引**，从物理上消除串台，并把"交互往返"抽象成一套通用 plumbing，AskQuestion 先迁入验证，Bohrium 以后接同一套。

## 2. 目标与非目标

### 2.1 目标
- 新建一套通用 per-request 交互传输底座：pending registry、per-request reply key、active 索引（含轻量互斥守卫）、阻塞等待 + `request_id` 配对。
- 底座对内层 payload **不透明**，只认 `(kind, request_id, payload)`；每种交互在自己那层定义 payload 形状与解析。
- AskQuestion 迁入：`AskQuestionBridge` 通用化为 `InteractionBridge`，AskQuestion 特有的 payload 构造/解析下沉为薄适配。
- 对外契约统一：SSE 事件用 `interaction_request|reply|timeout` + `kind`；回复用 `POST /chat/sessions/{session_id}/interactions/{request_id}/reply`。
- 前端 `scimaster-bohr-chat` 同步迁移到通用 interaction 契约（本 spec 含前后端）。
- 旧契约按"迁移而非兼容"删除，不留别名/兜底。

### 2.2 非目标
- 不实现 Bohrium submit review 的 provider / 闸门 / 工具知识（仅预留底座接入位）。
- 不为已存的 `ask_question*` 历史事件做兼容回放（按"无运行中用户"处理）。
- 不做重型分布式锁语义（无 fencing token、无自动续租）；active 互斥用 SETNX + TTL 兜底。
- **不引入新的 `run_id` 概念**：底座统一用现有 `task_id` 作为 run 维度标识（见 §5.6）。
- 不改 agent loop / fanout / SSE 推送机制 / session 状态机本身。

## 3. 现状代码事实（已核验）

| 关注点 | 位置 | 现状 |
|--------|------|------|
| 传输 bridge | `matmaster/integration/interaction_bridge.py` | `AskQuestionBridge`：`asyncio.Lock` + emit event + `to_thread` BLPOP；含 AskQuestion 语义（answers/annotations、request_id 校验） |
| 回复队列 | `src/services/stream_reply_queue.py` | `RedisReplyQueue`：put_content/put_cancel/get，基于 session 级共享 list |
| Redis list | `src/dao/redis_dao.py:22,56,248,260,274` | `chat:confirmation_reply:{session_id}` 共享 list；`rpush/blpop_interaction_reply`、`delete_interaction_reply_list`、`__CANCEL__` |
| session run 标志 | `src/dao/redis_dao.py:166,184,199,222,238` | `set/delete/is_interaction_run_active`、`set/get_interaction_run_context` |
| reply API | `src/apis/chat_api.py:487-516,519-555` | `_submit_interaction_reply`（只判 session 活跃，无 per-request 校验）；`POST /{sid}/ask_question_reply` |
| 取消 | `src/apis/chat_api.py:469-484` | stop 接口 `get_reply_queue(sid).put_cancel()` 投共享哨兵 + `stop_session_run(sid)` |
| stop 链路 | `src/services/sessions_service.py:668-684`、`src/worker/agent_worker.py:226-231` | `stop_session_run`：publish stop channel + `get_interaction_run_context` 拿 task_id + `set_stop_requested(sid, task_id)`；worker `_poll` 轮询 `is_stop_requested` → cancel token |
| reply queue 判定 | `src/services/stream_service.py:885-903` | `get_reply_queue`（靠 `is_interaction_run_active`）、`get_run_context`、`publish_reply_event` |
| 事件类型 | `matmaster/types/events.py:206-232,345-362` | `AskQuestionEvent/ReplyEvent/TimeoutEvent(EventBase)`；三者都在 `SystemEvent` discriminated union |
| 事件导出 | `matmaster/types/__init__.py:9-11,104-106` | 三个事件类显式导出 |
| 事件 payload | `matmaster/integration/event_payloads.py:370,379` | `_public_content_for_event` 对 ask_question / ask_question_reply 的投影 |
| 持久化 | `matmaster/integration/persistence_handler.py:87-89` | `_should_persist_type` 是**黑名单**（`event_type not in _SKIP_TYPES`），新 type 天然持久化；该改的是 `_public_content_for_event` 投影 |
| run 身份 | `matmaster/types/run_metadata.py:8-15` | `RunIdentity` 字段 = `task_id` / `session_id` / `spawn_id`，**无 run_id** |
| worker 注入 | `src/services/agent_run_service.py:507-515,573` | 建 `AskQuestionBridge` + `RedisReplyQueue`，`_interaction_event_sink → fanout.dispatch` |
| worker 生命周期 | `src/worker/agent_worker.py:372,380-381,416,532` | set/delete interaction_run_active/context；cleanup `delete_interaction_reply_list` |
| 工具注入 | `matmaster/core/exp.py:741-748` | `interaction_bridge = ... if spawn_id is None else None`；`AskQuestionTool(bridge=...)` |
| 进程内互斥 | `tool_scheduler.py:170`、`interaction_bridge.py:47,82`、`sessions_service.py:561` | 工具 `ResourceClaim(interaction, exclusive)` + bridge `asyncio.Lock` + session run acquire |

**关键事实**：多个 ask 的互斥，现状由"session 单 run + 工具 interaction 独占锁 + bridge asyncio.Lock"三重覆盖，**与共享 list 无关**；迁移后后两道进程内锁原样保留，互斥不丢。

## 4. 架构：三层

```
┌─ 接入层（每种交互各自接）────────────────────────────┐
│  AskQuestionTool（本次）   │  SubmitApprovalGate（未来）│
│  构造 questions payload    │  构造 submit draft payload  │
│  解析 answers/annotations  │  解析 submit_arguments...    │
└──────────┬──────────────────────────┬──────────────────┘
           │ kind="ask_question"      │ kind="submit_review"
           ▼                          ▼
┌─ 传输底座层（通用 · 本次新建 · payload 不透明）─────────┐
│  InteractionBridge.request(kind, request_id, payload,    │
│                            timeout) -> reply_payload      │
│    1. SETNX active 守卫   2. 写 pending registry          │
│    3. emit interaction_request 事件（经 fanout）          │
│    4. to_thread BLPOP 自己的 reply key                   │
│    5. 解析 / timeout / cancel   6. 清理（保留 terminal）   │
└──────────┬───────────────────────────────────────────────┘
           ▼
┌─ Redis DAO 层（per-request key）────────────────────────┐
│  pending registry / reply key / active 索引（session 维度）│
└──────────────────────────────────────────────────────────┘
       ▲ （另一侧）API 收回复 → 单条 Lua: 校验+终态+push reply
```

**边界**：传输底座对 payload 不透明，只搬运 + 等待 + 配对；内层 payload 由接入层定义。Bohrium 接入时底座零改动。

## 5. 核心设计决策

### 5.1 Redis key 结构

**删除**（共享 list 那套）：`chat:confirmation_reply:{session_id}`、`_reply_list_key`、`rpush/blpop_interaction_reply`（session 签名）、`delete_interaction_reply_list`、`INTERACTION_CANCEL_VALUE` 作为**共享 list** 哨兵的用法（哨兵值本身保留，改投 per-request reply key）。

**新建**：

| key | 类型 | 作用 |
|-----|------|------|
| `human_interaction:{request_id}` | hash + TTL | pending registry：`kind`/`session_id`/`task_id`/`invocation_id`/`state`/`expires_at`（hash 字段便于 state CAS） |
| `interaction_reply:{request_id}` | list + TTL | per-request 回复通道，worker 只 BLPOP 自己这条；承载 reply envelope 或 cancel 哨兵 |
| `human_interaction_active:{session_id}` | string=request_id + TTL | active 索引（**session 维度**）：取消定位 + 轻量 SETNX 互斥守卫 |

active 用 session 维度而非 `{session_id}:{task_id}`：session 级一次只有一个活跃 run（`try_acquire_session_run`）→ 一个 session 至多一个活跃交互，stop 入口只有 session_id 即可直接定位（见 §5.3、§6③）。

**清理与 TTL（吸收 review P1-a）**：registry 的 **terminal state（answered/timeout/cancelled）保留到 TTL**，不在 worker 正常路径立即删除——否则迟到/重复 reply 在 API 读不到 registry，无法区分"已超时/已答/已取消"与"根本不存在"，409 语义与排查失真。worker 正常结束只删 `interaction_reply:{request_id}` + 释放 active；registry 由 finalize 时设短 TTL（如 300s）兜底回收。reply key 同设 `timeout + buffer` TTL 防泄漏。

### 5.2 pending registry 状态机

```
   worker 发起 → 写 registry[pending] + emit 事件 + BLPOP reply key
                         │
       ┌─────────────────┼──────────────────┐
       │ API 收回复       │ BLPOP 超时        │ run stop
       │ Lua: →answered  │ finalize→timeout  │ finalize→cancelled
       │  + push reply   │                   │  + push 哨兵
       ▼                 ▼                  ▼
  [answered]         [timeout]          [cancelled]
  worker 取到返回     抛 TimeoutError      worker 取哨兵抛
                     接入层发 timeout      CancelledError
  （terminal 保留到短 TTL；迟到/重复 reply → API 据终态 409）
```

**state 是权威裁决者**。超时 vs 回复竞态用 registry state 的原子操作裁决：API answer 与 worker finalize-timeout 谁先改 terminal 谁赢；输家据终态返回明确错误（迟到回复 → 409 "已 answered/timeout/cancelled"）。现状共享 list 没有这层 API 入口仲裁。

### 5.3 active 索引 + 轻量互斥守卫

- active key（`human_interaction_active:{session_id}`）本次建，**取消定位刚需**：per-request 架构下 stop 只有 session_id，必须靠它拿到当前 active `request_id` 才能往对的 reply key 投哨兵唤醒 BLPOP。
- 附带 **SETNX 互斥守卫**：发起交互前 `SETNX active_key=request_id`，占用中则拒新交互（`InteractionBusyError`）；结束/取消时 **compare-and-delete**（只删 value==自己 request_id，防止删到下一轮交互的占用）。
- **定位说明**：本次"只有 AskQuestion + session 单 run"下，SETNX 实际不会触发拒绝（进程内 interaction 独占锁 + asyncio.Lock 已让 ask 串行）。其价值是 (a) 把"一个 run 一次一个活跃交互"不变量从隐式进程内锁**上提为显式 Redis 不变量**；(b) 为未来 ask+submit 跨 runner 阶段共存留唯一跨进程仲裁点；(c) 与取消索引共用一把 key。

### 5.4 对外契约统一

**SSE 事件**（统一 type + kind，前端 SSE 分发只认 3 个 type，按 kind 分流渲染）：

```jsonc
// interaction_request（worker→前端）
{ "type": "interaction_request", "kind": "ask_question",
  "request_id": "aq_xxx", "session_id": "...", "task_id": "...", "expires_at": "...",
  "payload": { "questions": [...], "metadata": {...}, "origin": "tool:AskQuestion",
               "preview_format": "markdown" } }

// interaction_reply（API→前端）  payload={answers, annotations}
// interaction_timeout（worker→前端）  reason="timeout"
```

**Reply endpoint**：

```
POST /chat/sessions/{session_id}/interactions/{request_id}/reply
body: { "kind": "ask_question", "payload": { "answers": {...}, "annotations": {...} } }
```

`request_id` 进 URL path；`task_id`/session 从 registry 校验（不进 URL）。API 校验 `kind` 与 registry 一致、`payload` 为 dict 且大小受限；内层 schema 由 worker 端 kind 适配解析时负责（payload 不透明原则）。

### 5.5 进程内互斥保留

通用化的 `InteractionBridge` 仍持 `asyncio.Lock`；`AskQuestionTool` 仍声明 `ResourceClaim(interaction, exclusive)`。session 级 `try_acquire_session_run` 不动。这三者保证多 ask 互斥不依赖 active lock。

### 5.6 run 维度术语：统一用 task_id（吸收 review P2-a）

当前 `RunIdentity` 只有 `task_id`/`session_id`/`spawn_id`，服务层主链路标识是 `task_id`/`invocation_id`，**不存在 run_id**。因此底座的 registry 字段、SSE 信封一律用 `task_id`，不杜撰 `run_id`。endpoint 也不带 run/task path 段（`request_id` 已足够定位，registry 内校验 session/task）。

> 与 `2026-06-17-bohrium-submit-review-design.md` 的对齐：那份草稿的 `POST .../runs/{run_id}/interactions/...` 以本底座为准修订——去掉 `runs/{run_id}` 段，统一为 `POST /chat/sessions/{session_id}/interactions/{request_id}/reply`。

## 6. 数据流

**① worker 发起**（`InteractionBridge.request`）：`SETNX active`（占用则 `InteractionBusyError`）→ 写 registry`[pending]` → emit `interaction_request`（经 `fanout.dispatch`）→ `to_thread` BLPOP `interaction_reply:{request_id}`（timeout=1800）→ 解析 envelope 返回。
- 正常/超时/取消的 `finally`：删 `interaction_reply:{request_id}` + compare-and-delete 释放 active；**registry 保留 terminal state**（finalize 时已设短 TTL）。
- 超时分支：worker `finalize_interaction(request_id, "timeout")`（仅当仍 pending）→ 抛 `TimeoutError` → 接入层发 `interaction_timeout`。

**② API 回复**（新 endpoint）：读 registry 做前置校验（`can_access_session` + `session_id`/`task_id` 匹配 + `kind` 匹配）→ **单条 Lua `answer_pending_interaction(request_id, envelope, ttl) -> enum`**：原子完成"校验 state==pending + 写 terminal=answered（短 TTL）+ RPUSH reply envelope=`{kind, request_id, payload}`"。enum：`ok`/`not_found`/`not_pending` → API 映射 200/404/409。Lua 返回 `ok` 后再 `publish_reply_event`（SSE 回显）+ `add_history_event`（持久化）。
- 吸收 review P1-c：CAS 与 push reply 合成一个原子操作，杜绝"已 answered 但 worker 没收到 reply"的硬挂起。

**③ 取消**（stop 接口）：`get_active_interaction(session_id)` 取当前 `request_id` → `finalize_interaction(request_id, "cancelled")`（仅当仍 pending）+ RPUSH cancel 哨兵到 `interaction_reply:{request_id}` → worker BLPOP 取哨兵抛 `CancelledError`。取代现状 `get_reply_queue(sid).put_cancel()`。
- `stop_session_run` 的 `set_stop_requested(sid, task_id)` 链路（cancel 整个 run）不变；task_id 仍由保留的 `get_interaction_run_context` 提供（见 §8）。

## 7. 组件与接口

### 7.1 InteractionBridge（通用传输底座）

```python
class InteractionBridge:
    def __init__(self, *, session_id, task_id, event_sink, dao, timeout_seconds=1800): ...

    async def request(self, *, kind: str, request_id: str,
                      payload: dict, timeout_seconds: int | None = None) -> dict:
        """发起一次交互并阻塞等待回复 payload。
        raise InteractionBusyError / TimeoutError / asyncio.CancelledError。"""
```

取代 `AskQuestionBridge` 的传输部分 + `RedisReplyQueue`。`event_sink` 仍接 `fanout.dispatch`。

### 7.2 Redis DAO 新函数（替换共享 list 那批）

- `write_pending_interaction(request_id, record: dict, ttl)` / `read_pending_interaction(request_id) -> dict | None`
- `answer_pending_interaction(request_id, envelope, terminal_ttl) -> enum`（**Lua 原子**：校验 pending + 写 answered + RPUSH reply）
- `finalize_interaction(request_id, state, terminal_ttl) -> bool`（**Lua 原子**：仅当 pending 时改 terminal=timeout/cancelled，幂等）
- `blpop_interaction_reply(request_id, timeout) -> str | None`（per-request key）
- `rpush_interaction_cancel(request_id)`（投 cancel 哨兵到 per-request key）
- `acquire_active_interaction(session_id, request_id) -> bool`（SETNX）/ `release_active_interaction(session_id, request_id)`（compare-and-delete）/ `get_active_interaction(session_id) -> str | None`
- `delete_interaction_reply(request_id)`（cleanup reply key）

### 7.3 AskQuestion kind 适配

`AskQuestionBridge` 删除；AskQuestion 特有逻辑（拼 `questions` payload、解析 `answers`/`annotations`、`request_id` 前缀 `aq_`、答案摘要渲染）下沉为薄适配，放 `AskQuestionTool` 内（现有 `_normalize_questions` / `_render_answer_summary` 已在此）。`AskQuestionTool` 改持有 `InteractionBridge`，`execute_with_context` 调 `bridge.request(kind="ask_question", payload={...})`。`exp.py:741-748` 注入路径改注入 `InteractionBridge`（spawn 仍为 None → 工具 `exposed_to_model=False`）。

### 7.4 通用事件类（`events.py`）

```python
class InteractionRequestEvent(EventBase):
    type: Literal["interaction_request"] = "interaction_request"
    kind: str; request_id: str; task_id: str
    expires_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

class InteractionReplyEvent(EventBase):
    type: Literal["interaction_reply"] = "interaction_reply"
    kind: str; request_id: str
    payload: dict[str, Any] = Field(default_factory=dict)

class InteractionTimeoutEvent(EventBase):
    type: Literal["interaction_timeout"] = "interaction_timeout"
    kind: str; request_id: str; reason: str = "timeout"
```

取代 `AskQuestionEvent/ReplyEvent/TimeoutEvent`，并更新 `SystemEvent` union（`events.py:345`）与 `types/__init__.py` 导出。`_public_content_for_event`（`event_payloads.py:370`）的投影改用新 type；`_should_persist_type` 是黑名单，**无需改**（interaction_* 天然持久化）。

### 7.5 API model（`models/chat.py`）

```python
class InteractionReplyRequest(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
```

取代 `ChatAskQuestionReplyRequest`。

## 8. 删除/迁移清单（迁移而非兼容）

**删除**：
- `AskQuestionBridge`、`RedisReplyQueue`
- `redis_dao`：共享 list 的 `_reply_list_key`/`INTERACTION_REPLY_LIST_KEY`/`delete_interaction_reply_list` 及 session 签名 rpush/blpop
- `chat_api`：`ask_question_reply` endpoint；`_submit_interaction_reply` 重写为通用
- `stream_service.get_reply_queue`（被新 API 读 registry 取代）
- `events.py`：`AskQuestionEvent/ReplyEvent/TimeoutEvent` + 从 `SystemEvent` union 移除 + `types/__init__.py:9-11,104-106` 导出
- `event_payloads.py`：ask_question / ask_question_reply 专用投影分支
- `models/chat.py`：`ChatAskQuestionReplyRequest`
- 前端 `ask_question` 专用 SSE 处理 / reply 调用

**保留（吸收 review P1-b）**：`get/set/delete_interaction_run_context`——`stop_session_run`（`sessions_service.py:676`）靠它拿 task_id 写 `set_stop_requested`，与交互无关，run 在跑就需要。

**待确认后删除**：`is/set/delete_interaction_run_active`——其唯一已知消费者是 `get_reply_queue`；该方法删除后若确认无其它消费即删，否则保留。净代码量目标：删旧（共享 list + bridge + get_reply_queue）抵消新建（per-request），不显著增加。

## 9. 前端改造范围（`scimaster-bohr-chat`，本 spec 含前端）

前端已有 `interaction` 抽象雏形，迁移是顺水推舟。关键文件与方向（writing-plans / 前端阶段细化）：

- `src/api/chat-evo-interaction.ts`、`src/api/chat-evo.ts`：reply 调用改新 endpoint + body `{kind, payload}`
- `src/pages/matmaster/chat-evo/hooks/evo-sse-handler/dispatch/content-events.ts`、`hooks/evo-sse-handler/types.ts`：SSE 分发改 `interaction_request|reply|timeout` + 按 `kind` 分流
- `src/pages/matmaster/chat-evo/utils/interaction.ts`：交互状态逻辑通用化
- `src/pages/matmaster/chat-evo/components/AskQuestionWizard.tsx`、`InteractionCard.tsx`：按 `kind` 渲染（ask_question 走 wizard）
- `tests/chat-evo/*interaction*.test.ts`、`ask-question-tool-result-linkage.test.ts`、`evo-sse-harness.ts`：适配新契约

无兼容期：前后端在同一迁移内协调一次切换。

## 10. 测试策略

迁旧测 + 核心新行为最小直测（不为通用化铺张）。

**迁移旧测（防半迁移，吸收 review P2-b）**——删 `AskQuestion*Event`/旧 endpoint 牵连：
- `tests/matmaster/types/test_events.py`（union/事件类）
- `tests/matmaster/integration/test_event_payloads.py`（投影映射）
- `tests/matmaster/core/test_exp.py`（bridge 注入）
- `tests/test_chat_stream_reply_events.py`、`tests/matmaster/apis/test_interaction_reply_api.py`（API route/model）
- `tests/matmaster/services/test_agent_run_stream_interaction.py`（fanout/persistence 链路）
- `tests/matmaster/tools/builtin/test_ask_question_tool.py`（含 timeout/cancel）
- 前端 `interaction.test.ts`、`ask-question-tool-result-linkage.test.ts`、`evo-sse-harness.ts`

**核心新行为最小直测**：
1. per-request 隔离：两个 request 的 reply 互不串台。
2. answer 原子性 + 终态：`answer_pending_interaction` 一次完成 state+push；重复/迟到 reply 据 terminal 返回 409。
3. 竞态：超时 finalize 与 answer 只有一个赢家。
4. **stop during AskQuestion 立即唤醒**：stop → `get_active_interaction` 定位 → 投哨兵 → BLPOP 立即返回 → `CancelledError`（不等 1800s）。后端必测。
5. active 守卫：SETNX 占用中拒新交互；compare-and-delete 不误删下一轮。
6. API 校验：request_id 不存在 → 404；kind 不匹配 / 非 pending → 409。

缺口（如完整 stale/duplicate 矩阵）先列不自动补。

## 11. 实施顺序建议

1. Redis DAO：per-request key 函数 + Lua（`answer_pending_interaction` / `finalize_interaction`）+ active（SETNX / compare-and-delete）；删共享 list 函数。
2. 通用事件类 `Interaction*Event` + 更新 `SystemEvent` union + `types/__init__` 导出 + `event_payloads` 投影；删 `AskQuestion*Event`。
3. `InteractionBridge`（通用传输底座，terminal 保留语义）；删 `AskQuestionBridge` / `RedisReplyQueue`。
4. AskQuestion kind 适配：`AskQuestionTool` 改持 `InteractionBridge`；`exp.py` 注入改。
5. API：新 `interactions/{request_id}/reply`（前置校验 + Lua answer）+ `InteractionReplyRequest`；stop 取消改 `get_active_interaction` + finalize + 投哨兵；删旧 endpoint/model/`get_reply_queue`。
6. worker：`agent_run_service` 建 `InteractionBridge`（传 task_id）；`agent_worker` cleanup 改 per-request；保留 run_context，评估删 run_active。
7. 前端：SSE 分发 + reply 调用 + 渲染 + tests 迁移。
8. 测试：迁旧 + 核心新行为直测（含 stop 唤醒必测）。

## 12. 风险与边界

- **stop 闭合**：active 用 session 维度后，stop 仅凭 session_id 即可定位 active request 并唤醒 BLPOP；`set_stop_requested` 的 task_id 由保留的 `get_interaction_run_context` 提供。stop-during-AskQuestion 立即唤醒为必测项。
- **terminal 可见性**：terminal state 保留到短 TTL，保证迟到/重复 reply 得到明确 409 而非"不存在"；TTL 过期后回归"不存在"，可接受。
- **answer 原子性**：`answer_pending_interaction` 用 Lua 保证 state+push 不可分割，无硬挂起窗口。
- **run_active 删除**：仅在确认 `is_interaction_run_active` 无 `get_reply_queue` 以外消费后删，否则保留。
- **前后端切换窗口**：无兼容，须协调同时上线。
- **payload 不透明 vs 校验**：内层 schema 校验下放接入层，API 仅做 kind 匹配 + 大小限制。
