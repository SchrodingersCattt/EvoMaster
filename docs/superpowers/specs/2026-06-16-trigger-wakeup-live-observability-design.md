# Trigger Wakeup Live Observability Design

日期：2026-06-16
状态：设计已写入，待评审后拆分 implementation plan。

## 1. 背景

`trigger_run` 已经完成过一次前后端接线：后端在后台 trigger 入队后向用户级
`wakeup` channel 发布 `session_wakeup`，前端打开用户级 wakeup stream，收到当前
session 的 wakeup 后重开会话级 `/stream`。

线上复现显示，当前链路仍存在一个 refresh-only 问题：

1. 未刷新页面时，Bohrium job 卡片保持旧状态，页面没有出现后台 trigger 的 live
   response。
2. 刷新后，history replay 能看到完整的 Monitor/agent response。
3. 后端 API/worker/monitor 日志证明 trigger 实际已经成功执行。

这意味着问题不在 agent 没跑，也不在历史没写入，而在后台 trigger 完成前，当前页面
没有被 wakeup 驱动去重新打开会话级 `/stream`。

## 2. 已验证事实

以下事实来自 2026-06-15 线上复现日志，时间均为 CST。

### 2.1 API/Worker 日志

目标 session：

```text
385f4fee5e5e4adc801485fc9b8fc9fd
```

目标 job：

```text
94273ce59be1460ca889f22c8d5b86ee
```

关键时间线：

```text
22:14:21 API 收到一次 no-content /stream，status=idle
22:19:41 Worker 启动 trigger run，task_id=trig_8fb4b441a4e44605
22:19:49 Worker 完成 trigger run，session release 为 idle
22:21:33 API 才收到刷新后的 no-content /stream，status=idle，并通过 history replay 看到结果
```

结论：

- trigger worker 确实执行了。
- trigger 结果已经写入 history。
- `22:19:41` 到 `22:19:49` trigger 活跃期间，API 没有收到前端重新打开的
  `/stream` 请求。

### 2.2 Monitor 日志

monitor 在目标窗口的关键日志：

```text
22:19:41.537 matmaster-monitor: bohrium {'claimed': 1, 'polled': 1, 'errors': 0}
22:19:41.588 set_session_run_owner session_id=385f4fee5e5e4adc801485fc9b8fc9fd
22:19:41.589 try_acquire_session_run acquired session_id=385f4fee5e5e4adc801485fc9b8fc9fd
22:19:41.603 discard_session_run_from_this_pod session_id=385f4fee5e5e4adc801485fc9b8fc9fd
22:19:41.690 matmaster-monitor: delivery {'scanned': 13, 'eligible': 12, 'triggered': 1, 'skipped_identity': 8, 'skipped_busy': 0, 'skipped_failed': 3, 'skipped_redis': 0, 'errors': 0, 'tick_failed': 0}
```

结论：

- monitor 存活且持续 tick。
- monitor 确实 poll 到一个 Bohrium job 终态。
- delivery scheduler 成功触发一次 run。
- `skipped_redis=0`、`errors=0`、`tick_failed=0`，monitor 侧没有可见失败。

### 2.3 当前观测盲区

当前代码里：

- `RedisDao.publish_user_wakeup(...)` 只返回 bool，不暴露 Redis `PUBLISH` 的订阅者数量。
- `_publish_user_wakeup(...)` 只在失败时打 warning，成功时不打日志。
- `generate_wakeup_stream(...)` 没有 connect、snapshot、live forward、disconnect 日志。
- 前端 `openWakeupStream(...)` 没有 open、connected、event、close、retry 的结构化日志。

所以日志无法回答这些关键问题：

```text
publish_user_wakeup 是否实际调用？
Redis PUBLISH 当时 subscriber_count 是多少？
/wakeup/stream 当时是否存在活跃订阅？
API wakeup generator 是否收到并转发 live event？
前端是否收到 wakeup frame？
前端收到后是否触发 requestEnsureSessionStreamOpen？
```

## 3. 问题陈述

当前系统把用户级 wakeup 当作临时 Redis pub/sub 事件。Redis pub/sub 的语义是实时广播，
没有持久化和补偿。如果发布时没有订阅者，或者订阅者存在但 API/前端任一层断开，事件
就会丢失。

现有 snapshot 只覆盖 `waiting` 和 `active` session。目标 trigger run 只运行了约 7.5s，
很快回到 `idle`。因此即使前端稍后重连 wakeup stream，waiting/active snapshot 也可能
已经看不到这次后台 trigger，只能靠用户刷新页面后从 history replay 看到结果。

这解释了当前现象：

```text
trigger 已执行并写 history
live 页面没动
刷新后可见
```

## 4. 目标

- 让下一次复现能精确定位断点：publish、Redis subscriber、API wakeup stream、前端
  SSE、前端重开 `/stream`。
- 保留用户级 wakeup stream 只负责发现的架构，不把完整 agent response 放进 wakeup。
- 对 Redis pub/sub 的临时事件增加短 TTL 补偿，使短生命周期 trigger 不再只能依赖
  当前瞬间在线的订阅者。
- 不改变浏览器可见的 `session_wakeup` payload 四字段协议。
- 不引入主代码内联迁移或兼容分支；项目仍在开发阶段，测试按新语义直接迁移。

## 5. 非目标

- 不把会话级 `/stream` 改成长驻 session 生命周期流。
- 不让前端轮询完整 chat history。
- 不实现站内通知中心、未读表、跨设备通知历史或离线消息中心。
- 不把 job card 的 `Status Pending` 旧快照问题并入本设计。该问题可能来自历史
  tool result 的 immutable 快照，需单独检查前端 job card 的数据源。
- 不向浏览器暴露 `task_id`、`invocation_id`、`origin`、prompt 或 job 明细。

## 6. 方案比较

### 方案 A：只补前端重试或定时刷新

做法：前端发现 job pending 时定时刷新 session 或重新打开 `/stream`。

优点：

- 改动集中在前端。
- 对用户当前 session 有直接效果。

缺点：

- 会把 Bohrium job 状态和 trigger wakeup 语义混在一起。
- 对非 job trigger 不通用。
- 容易退化成隐式 polling，增加 API 和 history replay 压力。
- 不能解释当前 wakeup 链路到底断在哪里。

结论：不采用。

### 方案 B：只补日志，先不修复

做法：后端和前端都增加观测日志，复现后再决定修复点。

优点：

- 完全符合证据先行。
- 风险最低。

缺点：

- 即使复现成功，也只是知道断点，用户体验仍然会 refresh-only。
- 如果根因是 Redis pub/sub 无订阅者，日志只能证明丢失，不能补偿丢失。

结论：作为第一阶段必须做，但不能作为完整方案。

### 方案 C：观测日志 + 短 TTL durable wakeup 补偿

做法：

1. 后端 publish 时记录 subscriber_count、channel、session_id、reason。
2. 后端 wakeup stream 记录 connect、snapshot、live forward、reconcile、disconnect。
3. 前端记录 wakeup stream open、connected、event、dispatch、close、retry。
4. 后端把每次 user wakeup 同步写入 Redis 短 TTL recent set/list。
5. wakeup stream 建连时和空闲 keepalive 时，读取 recent wakeup，向浏览器补发尚未在本连接
   发送过的 wakeup payload。
6. 浏览器可见 payload 仍然只有四字段；内部 durable envelope 的 id 和时间戳只在 Redis/API
   内部使用。

优点：

- 保留证据链，能回答具体断点。
- 解决 Redis pub/sub 事件在无订阅者或短暂断线时丢失的问题。
- 不改变前端 wakeup 协议，不把 run 详情塞进 wakeup。
- 对短生命周期 trigger 友好，即使 session 很快回到 idle，也能靠 recent wakeup 补发。

缺点：

- 后端 Redis DAO 多一个 recent wakeup 数据结构。
- wakeup stream loop 需要维护本连接已发送 wakeup id，避免同一连接内重复补发。
- 前端仍需保持幂等，因为重连后同一个 recent wakeup 可能再次出现。

结论：推荐采用。

## 7. 推荐架构

整体仍然是两层流：

```text
用户级 wakeup stream：发现某个 session 需要关注
会话级 /stream：承载 history replay、System/trigger、agent response、stream_closed
```

新增一层短 TTL recent wakeup 作为 Redis pub/sub 的补偿：

```text
trigger enqueue
  -> record recent wakeup envelope in Redis
  -> Redis PUBLISH user wakeup envelope
  -> active /wakeup/stream 收到 live 后转成四字段 payload 给浏览器

/wakeup/stream connect
  -> subscribe user channel
  -> emit waiting/active snapshot
  -> emit recent wakeups not seen by this connection
  -> loop: live event 或定期 reconcile recent wakeups
```

浏览器只看到：

```json
{
  "source": "System",
  "type": "session_wakeup",
  "reason": "trigger_enqueued",
  "session_id": "385f4fee5e5e4adc801485fc9b8fc9fd"
}
```

Redis 内部 envelope 可以是：

```json
{
  "id": "wku_8f1b2c3d4e5f",
  "created_at_ms": 1781533181588,
  "payload": {
    "source": "System",
    "type": "session_wakeup",
    "reason": "trigger_enqueued",
    "session_id": "385f4fee5e5e4adc801485fc9b8fc9bdd"
  }
}
```

`id` 和 `created_at_ms` 不下发给浏览器。

## 8. 后端设计

### 8.1 Redis publish 结果

修改 `src/dao/redis_dao.py`：

- 为 user wakeup 增加专用 result dataclass，例如 `UserWakeupPublishResult`。
- `publish_user_wakeup` 不再只返回 bool，而是返回：

```python
@dataclass(frozen=True)
class UserWakeupPublishResult:
    ok: bool
    channel: str
    subscriber_count: int | None
```

语义：

- `ok=True`：Redis publish 命令执行成功。
- `subscriber_count=0`：publish 成功，但当时没有订阅者。
- `subscriber_count>0`：publish 成功，Redis 报告有订阅者收到广播。
- `subscriber_count=None`：没有 Redis client 或 publish 抛异常。

不建议改通用 `RedisDao.publish(...)` 的返回值，避免影响会话级 stream event 的既有调用面。
只让 `publish_user_wakeup(...)` 直接调用 `client.publish(...)` 并拿到返回的订阅者数量。

### 8.2 Recent wakeup 存储

修改 `src/dao/redis_dao.py`，新增用户级 recent wakeup key：

```text
chat:user:{user_id}:wakeup_recent
```

建议使用 Redis sorted set：

- member：envelope JSON。
- score：`created_at_ms`。
- TTL：默认 10 分钟，可用 `USER_WAKEUP_RECENT_TTL_SECONDS` 配置，默认值 `600`。

新增方法：

```python
def record_user_wakeup(self, user_id: str, payload: dict, *, ttl_sec: int) -> dict | None:
    ...

def list_recent_user_wakeups(self, user_id: str, *, now_ms: int, ttl_sec: int) -> list[dict]:
    ...
```

`record_user_wakeup` 返回 envelope；Redis 不可用或 JSON 失败返回 None 并 warning。wakeup
仍不回滚 trigger。

`list_recent_user_wakeups` 读取最近 TTL 内的 envelope，并顺手清理过期 score。返回值只供
API generator 内部使用。

### 8.3 publish 顺序

修改 `src/services/stream_service.py` 的 `_publish_user_wakeup(...)`：

推荐顺序：

```text
构造四字段 payload
record recent wakeup envelope
publish envelope 到 user wakeup channel
记录 info 日志
```

日志字段：

```text
event=wakeup_publish
user_id
session_id
reason
channel
recorded=true/false
subscriber_count
publish_ok
```

如果 record 成功但 publish subscriber_count 为 0，日志不是 warning，而是 info：

```text
wakeup_publish ok user_id=... session_id=... subscriber_count=0 recorded=true
```

因为无订阅者不是服务错误，recent wakeup 会负责补偿。

如果 publish 失败，保留 warning：

```text
wakeup_publish failed user_id=... session_id=... channel=...
```

### 8.4 wakeup stream connect/live/reconcile 日志

修改 `ChatStreamService.generate_wakeup_stream(...)`：

每个连接生成一个短 connection id：

```python
connection_id = "wku_conn_" + uuid.uuid4().hex[:8]
```

记录：

```text
wakeup_stream connect user_id=... connection_id=... channel=...
wakeup_stream snapshot user_id=... connection_id=... waiting_active_count=... recent_count=...
wakeup_stream live user_id=... connection_id=... session_id=... reason=... wakeup_id=...
wakeup_stream reconcile user_id=... connection_id=... recent_count=... emitted_count=...
wakeup_stream disconnect user_id=... connection_id=...
```

live channel 内部建议发送 envelope，generator 从 envelope 取 `payload` 下发给浏览器。

如果读取到非法 envelope：

```text
wakeup_stream invalid_envelope user_id=... connection_id=...
```

并丢弃该条，不中断连接。

### 8.5 reconcile 机制

当前 loop 空闲 30 秒发 keepalive：

```python
payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
```

调整为：

1. timeout 时先读 recent wakeups。
2. 对本连接未发送过的 envelope id，下发其四字段 payload。
3. 如果本轮没有补发，再发 keepalive comment。

本连接维护：

```python
seen_wakeup_ids: set[str]
```

live event 下发后，把 envelope id 加入 `seen_wakeup_ids`。snapshot/reconcile 补发后也加入。
这样同一个长连接内不会重复补发；浏览器重连后可能再次收到 TTL 内 recent wakeup，前端必须
继续保持幂等。

### 8.6 session_waiting_snapshot 与 recent wakeup 的关系

保留现有 waiting/active snapshot：

```text
reason=session_waiting_snapshot
```

新增 recent wakeup 补发：

```text
reason=trigger_enqueued
```

两者都下发同一个四字段 browser payload schema，但来源不同：

- waiting/active snapshot 表示当前仍有在途 run。
- recent wakeup 表示近期发生过后台 trigger，即使 session 已回到 idle，也值得当前页面
  打开 `/stream` 做一次 replay。

## 9. 前端设计

前端仓库：

```text
/Users/kealdoom/Developer/dp/matmaster/scimaster-bohr-chat
```

### 9.1 日志补点

修改：

```text
src/pages/matmaster/chat-evo/features/wakeup/wakeup-client.ts
src/pages/matmaster/chat-evo/features/wakeup/useWakeupStream.ts
src/pages/matmaster/chat-evo/features/wakeup/wakeup-dispatch.ts
src/pages/matmaster/chat-evo/features/wakeup/wakeup-store.ts
src/pages/matmaster/chat-evo/index.tsx
```

日志建议统一前缀：

```text
[wakeup-stream]
[wakeup-dispatch]
[wakeup-session-stream]
```

关键日志：

```text
[wakeup-stream] open hasExplicitUserId=... isLoggedIn=...
[wakeup-stream] connected status=...
[wakeup-stream] event sessionId=... reason=...
[wakeup-stream] close willRetry=...
[wakeup-stream] error ...
[wakeup-dispatch] current-session ensure-open sessionId=... reason=...
[wakeup-session-stream] reopen sessionId=... nonce=...
```

日志不得输出 token、Authorization、cookie、access key。

### 9.2 幂等处理

recent wakeup 可能在重连后重复出现。前端应保留轻量去重：

```text
dedupe key = sessionId + reason
window = 2 分钟
```

对当前 session：

- 如果对应 session stream 已经 `connecting`，不重复自增 nonce。
- 如果已是 `open` 或 `idle`，收到 `trigger_enqueued` 可以确保重开一次 `/stream`，由
  history replay 去判断是否有新事件。

对非当前 session：

- `trigger_enqueued` 标记侧栏关注态。
- `session_waiting_snapshot` 只恢复状态，不弹新提示。

### 9.3 不改变 browser payload parser

`wakeup-protocol.ts` 仍只接受：

```text
source
type
reason
session_id
```

如果后端 recent envelope 正确剥离，前端不应看到 `id` 或 `created_at_ms`。测试应断言
看到额外字段时 parser 拒绝，避免内部 envelope 泄漏成浏览器协议。

## 10. 测试策略

### 10.1 后端单元测试

新增或修改：

```text
tests/test_redis_dao_user_wakeup.py
tests/test_wakeup_stream.py
tests/test_agent_run_trigger.py
```

覆盖：

- `publish_user_wakeup` 返回 subscriber_count。
- `subscriber_count=0` 不算失败。
- `_publish_user_wakeup` 先 record recent，再 publish。
- record 失败不回滚 trigger。
- `generate_wakeup_stream` connect snapshot 包含 waiting/active。
- `generate_wakeup_stream` connect snapshot 包含 recent wakeups。
- live envelope 下发时只把 payload 发给浏览器。
- timeout reconcile 补发未 seen 的 recent wakeups。
- 同一连接内 seen id 不重复下发。
- invalid envelope warning 后丢弃，不断开连接。

建议命令：

```bash
uv run pytest tests/test_redis_dao_user_wakeup.py tests/test_wakeup_stream.py tests/test_agent_run_trigger.py -v
```

### 10.2 后端集成验证

使用 `.env.test` 和 docker 本地环境时，验证顺序：

```text
启动 API、worker、monitor、Redis、MySQL
打开一个 session 页面但不刷新
提交 sleep 180 Bohrium job
等待 monitor trigger
检查日志中 wakeup_publish subscriber_count
检查 wakeup_stream live 或 reconcile
检查 API 在 trigger active 窗口是否收到 no-content /stream
```

验收关键点：

```text
monitor delivery triggered=1
wakeup_publish publish_ok=true
wakeup_stream live 或 reconcile emitted_count>0
frontend wakeup event sessionId=目标 session
API /stream 在刷新前出现
页面在刷新前出现 Monitor response
```

### 10.3 前端测试

新增或修改：

```text
tests/chat-evo/wakeup-protocol.test.ts
tests/chat-evo/wakeup-dispatch.test.ts
tests/chat-evo/wakeup-store.test.ts
```

覆盖：

- parser 仍拒绝多字段 browser payload。
- 重复 `trigger_enqueued` 在去重窗口内不重复触发 current session reconnect。
- `session_waiting_snapshot` 不弹 live notification。
- `trigger_enqueued` 对当前 session 触发 ensure-open。
- `trigger_enqueued` 对非当前 session 标记 attention。

建议命令：

```bash
pnpm run test:chat-evo -- wakeup
```

如果项目测试入口不支持参数过滤，则运行现有 chat-evo 测试集合。

## 11. 线上观测与验收

下一次线上复现时，后端日志应该能直接串出：

```text
matmaster-monitor: delivery ... triggered=1
wakeup_publish ... session_id=... subscriber_count=...
wakeup_stream live ... session_id=...
或 wakeup_stream reconcile ... emitted_count=...
POST /api/v1/chat/sessions/{session_id}/stream has_content=False
```

前端 console 应能串出：

```text
[wakeup-stream] event sessionId=... reason=trigger_enqueued
[wakeup-dispatch] current-session ensure-open sessionId=...
[wakeup-session-stream] reopen sessionId=... nonce=...
```

判定：

- 如果 `subscriber_count=0` 且后续 reconnect/reconcile 补发成功，说明根因是发布瞬间无订阅者
  或连接断开，durable recent 生效。
- 如果 `subscriber_count>0` 但没有 `wakeup_stream live`，问题在 API Redis subscribe 或
  channel/envelope decode。
- 如果后端有 `wakeup_stream live/reconcile`，但前端无 `[wakeup-stream] event`，问题在
  浏览器 SSE 连接或 `readSSEStream`。
- 如果前端有 event，但 API 没有新的 `/stream`，问题在前端 dispatch 到
  `useLoadHistoryStream` 的 nonce 驱动链。

## 12. 风险与边界

- recent wakeup 是短 TTL 补偿，不是通知历史。TTL 到期后不保证补发。
- Redis 不可用时，agent queue 本身也不可用；wakeup record/publish 失败只记录日志，不回滚
  trigger 语义。
- 重连后可能重复收到同一 recent wakeup；前端和后端连接内 seen set 共同降低重复，但业务上仍要
  把 ensure-open 视为幂等动作。
- `Status Pending` 的旧 job card 问题不由本设计直接解决；如果 live response 已出现但 job card
  仍旧，需要检查前端卡片是否读取历史 tool result 快照而非最新 job 状态。

## 13. 文件边界

后端仓库：

```text
/Users/kealdoom/Developer/dp/matmaster/matmaster-evo
```

建议修改：

```text
src/dao/redis_dao.py
src/services/stream_service.py
tests/test_redis_dao_user_wakeup.py
tests/test_wakeup_stream.py
tests/test_agent_run_trigger.py
```

前端仓库：

```text
/Users/kealdoom/Developer/dp/matmaster/scimaster-bohr-chat
```

建议修改：

```text
src/pages/matmaster/chat-evo/features/wakeup/wakeup-client.ts
src/pages/matmaster/chat-evo/features/wakeup/useWakeupStream.ts
src/pages/matmaster/chat-evo/features/wakeup/wakeup-dispatch.ts
src/pages/matmaster/chat-evo/features/wakeup/wakeup-store.ts
src/pages/matmaster/chat-evo/index.tsx
tests/chat-evo/wakeup-protocol.test.ts
tests/chat-evo/wakeup-dispatch.test.ts
tests/chat-evo/wakeup-store.test.ts
```

## 14. 后续执行顺序

1. 后端补 publish result、recent wakeup DAO 和 wakeup stream 观测日志。
2. 后端测试通过后部署 test。
3. 前端补 wakeup stream 和 dispatch 日志、去重窗口。
4. 前端测试通过后部署 test。
5. 在同一个 Bohrium sleep 任务上复现。
6. 用后端日志、前端 console 和 API `/stream` 请求时间线判断是否已经修复。
7. 如果页面 live response 出现但 job card 仍 pending，另开 job card 状态同步设计。

## 15. 自检

- 没有改变浏览器可见 wakeup payload 协议。
- 没有把完整 agent response 放进 user wakeup stream。
- 没有依赖用户刷新页面。
- 没有把普通用户发送路径纳入 wakeup publish。
- recent wakeup 只作为短 TTL 补偿，不是持久通知中心。
- 设计明确区分 live response 问题与 job card pending 问题。
