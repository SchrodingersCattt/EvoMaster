# Trigger 前端感知设计：用户级 wakeup stream + 会话级 run stream

日期：2026-06-14
状态：已确认方案 C，待实现计划。

## 1. 背景

当前 `trigger_run` 是后端程序化唤醒原语，典型来源包括 Bohrium
completion monitor、loop、schedule 或外部内部调用。它会把一次后台触发转换为
普通 agent run，并写入 `System/trigger` 会话历史事件。

当前链路存在两个前端感知问题：

1. 后台 monitor 直接调用 `trigger_run`，没有浏览器 HTTP response，因此不会自动给
   前端建立 SSE 渠道。
2. 内部 HTTP trigger 路径在成功入队后才返回 `generate_subscribe_stream`，而普通
   用户发送路径是先建立 Redis 订阅再入队。内部 trigger 因此存在
   subscribe-after-enqueue 竞态，Worker 很快发布事件时，当前调用方的实时 SSE 可能
   错过早期事件。

本设计解决两个层面的目标：

1. 当前打开的会话页能感知后台 trigger，并自动补接会话流。
2. 用户打开任意 MatMaster 页面时，也能知道用户名下某个 session 被后台唤醒。

## 2. 目标

- 新增用户级 wakeup stream，让任意 MatMaster 页面都能收到 session 唤醒信号。
- 保持现有会话级 `/chat/sessions/{session_id}/stream` 作为唯一完整 run 内容通道。
- 当前会话页收到自己的 wakeup 后，自动打开或重开 session stream，并通过 history
  replay 看到 `System/trigger`。
- 非当前会话收到 wakeup 后，只更新会话列表、侧栏或提示态，不打开该会话详情流。
- 修正内部 HTTP trigger 的 subscribe-after-enqueue 竞态，使该 HTTP 调用方自己的
  SSE 满足 subscribe-before-enqueue。
- 第一版 wakeup payload 严格保持最小字段，不暴露 `task_id`、`invocation_id`、
  `status`、`origin`。

## 3. 非目标

- 不实现浏览器 Push、飞书、邮件或离线提醒。
- 不实现站内通知中心、持久未读表或跨设备通知历史。
- 不让用户级 wakeup stream 承载 thought、response、tool_call、run_result 等 run
  详情事件。
- 不把 session `/stream` 改成长驻会话生命周期流。
- 不为每个 session 同时打开会话详情流。
- 不让前端根据 wakeup payload 构造聊天消息。
- 不通过兼容字段支持旧前端协议。项目仍在开发阶段，协议直接按本设计收敛。

## 4. 核心方案

采用两层流：

1. 用户级 wakeup stream 负责发现：告诉前端某个 session 需要重新关注。
2. 会话级 run stream 负责内容：继续由现有 session `/stream` 提供 status、history
   replay、`System/trigger`、agent 输出与 `stream_closed`。

用户级 wakeup stream 不替代会话流。它只驱动前端做一次幂等动作：

```text
ensureSessionStreamOpen(session_id)
```

如果 wakeup 的 `session_id` 是当前会话，前端打开或确保打开 session stream。如果不
是当前会话，前端只标记该会话需要关注。

## 5. Wakeup 事件协议

第一版只保留四个字段：

```json
{
  "source": "System",
  "type": "session_wakeup",
  "reason": "session_waiting_snapshot",
  "session_id": "sess_xxx"
}
```

字段语义：

| 字段 | 语义 |
| --- | --- |
| `source` | 固定为 `System`，表示系统级通知。 |
| `type` | 固定为 `session_wakeup`，表示用户级 session 唤醒信号。 |
| `reason` | 唤醒原因。第一版只用于区分 live 与 snapshot。 |
| `session_id` | 唯一业务关键字段。前端根据它决定是否打开当前会话流或标记其他会话。 |

第一版 reason 枚举：

| reason | 产生时机 | 前端建议行为 |
| --- | --- | --- |
| `trigger_enqueued` | 后台 trigger 成功入队后 live 推送。 | 当前 session 打开 session stream；非当前 session 可标未读或轻提示。 |
| `session_waiting_snapshot` | wakeup stream 建立或重连时，后端发现该用户仍有 waiting 或 active session。 | 只恢复状态，不弹新通知。 |

禁止加入的字段：

```text
task_id
invocation_id
status
origin
created_at_ms
prompt
content
```

这些字段要么属于会话详情流，要么属于后端内部运行标识。用户级 wakeup stream 不消费
这些信息。

## 6. 后端数据流

### 6.1 Live wakeup 发布点

`trigger_run` 成功路径调整为：

```text
校验 session owner
检查 dedup
写 System/trigger 到 chat history
组装 job
_enqueue_run 成功
publish_user_wakeup(user_id, session_id, reason="trigger_enqueued")
返回 TriggerResult(status="enqueued")
```

live wakeup payload：

```json
{
  "source": "System",
  "type": "session_wakeup",
  "reason": "trigger_enqueued",
  "session_id": "sess_xxx"
}
```

发布条件收敛为单一判定：当且仅当 `trigger_run` 返回 `status == "enqueued"` 时
publish，其余一律不发。

`TriggerResult.status` 实际只有四个取值：`enqueued`、`deduped`、`busy`、`error`
（`enqueue_failed`、`session_not_found_or_no_owner` 都是 `error` 的 reason，不是独立
status）。额度不足由 API 层在进入 `trigger_run` 之前抛异常，根本不产生 status，因此也
不会 publish。所以不必逐个枚举失败原因：

```text
status == "enqueued"  -> publish
其他任何 status        -> 不 publish
```

deduped、busy、error 都没有产生新的可消费 run，前端不应被打扰。

发布点约束：`publish_user_wakeup` 必须挂在 trigger 语义点，严禁下沉到 `_enqueue_run`。
`_enqueue_run` 是普通用户前台发送、后台 trigger、内部 HTTP trigger 三条路径共享的入队
核心。若把 publish 写进 `_enqueue_run`，普通用户自己发一条消息也会向自己的 user
channel 推 wakeup，而 wakeup 协议里并没有“前台发送”这一类 reason。因此 publish 只允许
出现在两处 trigger 入口：`trigger_run`（本节）与 `generate_internal_trigger_stream`
（6.6），两处共用同一个 `publish_user_wakeup(user_id, session_id, reason)`，统一
payload 构造，不各写一遍。

### 6.2 Publish 失败处理

wakeup publish 失败不回滚 trigger。

处理规则：

```text
记录 warning
trigger_run 仍返回 enqueued
依赖 wakeup stream 重连 snapshot 或用户进入 session 后的 history replay 恢复
```

wakeup 是前端感知加速层，不是 agent run 的提交条件。

### 6.3 Redis channel

新增用户级 Redis channel：

```text
chat:user:{user_id}:wakeup
```

API 进程上的用户级 SSE 订阅该 channel 并转发给浏览器。后台 monitor 或 Worker 不需要
知道用户当前打开了哪个页面。

### 6.4 Wakeup stream endpoint

新增登录用户接口：

```text
GET /api/v1/chat/wakeup/stream
```

行为：

1. 仅允许登录用户访问：用 `require_user_id`（强制登录），不能用现有 session `/stream`
   的 `optional_user_id`。
2. 不支持 share route：挂在 api_router（`/api/v1`）下，绝不挂到 share_router
   （`/pubapi/v1`），从路由层杜绝匿名或分享访问。
3. 建连后先发送 snapshot wakeup。
4. 然后订阅 `chat:user:{user_id}:wakeup`，转发 live wakeup。
5. 网络异常或客户端断开时结束 generator，释放 Redis subscription。

### 6.5 Snapshot 恢复

用户级 wakeup stream 不能只靠 Redis pub/sub，因为浏览器刷新、休眠、断线或 API
重启时会错过 live event。建连和重连时，后端发送轻量 snapshot。

第一版 snapshot 来源：

```text
当前用户下 status 为 waiting 或 active 的 session
```

session status 实际有四个取值：`idle`、`active`、`waiting`、`failed`。snapshot 只取
`waiting` 与 `active`。`idle` 与 `failed` 都代表上一轮已结束（failed 是被
deploy/restart 中断后按失败收尾的态），没有仍需关注的在途 run，因此不纳入 snapshot；
用户进入该 session 后，由 session stream 的 history replay（含 `run_interrupted`）
展示完整结局。

当前代码没有“按 user_id 过滤 status”的现成查询：`list_sessions` 不按 status 过滤，
`count_active_sessions` 不按 user 过滤。第一版需新增 DAO 查询，例如
`list_sessions_by_status(user_id, statuses=["waiting", "active"])`。

每个 session 发一条：

```json
{
  "source": "System",
  "type": "session_wakeup",
  "reason": "session_waiting_snapshot",
  "session_id": "sess_xxx"
}
```

snapshot 条数上界等于该用户当前 waiting/active session 数。活跃用户重连时可能一次性收到
数十条，前端 reducer 必须保持幂等（见 7.2），不因重复 session_id 重复打开 stream。

不从 chat history 或 notification history 回放 wakeup。第一版不新增 notification
table。

如果某个 run 已完成且 session 回到 idle，wakeup snapshot 不负责恢复它。用户进入该
session 后，由 session stream 的 history replay 展示完整历史。

### 6.6 内部 HTTP trigger 的时序修正

内部 HTTP trigger 当前是 enqueue-then-subscribe：先在 `trigger_run` 内部
`_enqueue_run` 入队，返回 `status == "enqueued"` 后才进 `generate_subscribe_stream`
建立订阅。竞态窗口精确地说是：订阅建立之前、Worker 已把早期事件 publish 到
`chat:stream:{session_id}`、但这些事件尚未落库的那一段。已落库的事件会被
`generate_subscribe_stream` 开头的 history replay 兜底；只有这段“已发布未落库”的早期
live 事件会被当前 HTTP 调用方错过。

修正方式是对齐普通发送路径的 subscribe-before-enqueue，把链路拆成 prepare 与 generator
两段：

```text
prepare_internal_trigger_run
  - 校验 owner / quota / Redis
  - 占用 session
  - 写 System/trigger
  - 组装 job
  - 不入队

generate_internal_trigger_stream
  - 推送 status + history + System/trigger
  - 建立 session Redis stream 订阅并等待 subscribe_ready
  - _enqueue_run(job)
  - publish_user_wakeup(user_id, session_id, reason="trigger_enqueued")
  - 转发 Worker live events
```

这里“等待 subscribe_ready”沿用普通发送路径的既有语义，是 best-effort 软等待而非强阻塞：
`subscribe_ready` 带 3s 超时，超时只记 warning 并照常入队，不阻断 run。修正目标是把竞态
窗口收敛到与普通发送路径同等水平，而不是引入新的强同步保证。

`publish_user_wakeup` 与 6.1 共用同一函数，在 generator 段、`_enqueue_run` 成功之后
调用；prepare 段不入队也不 publish。

后台 monitor 直接调用 `trigger_run` 仍可同步入队。它没有浏览器 response，前端感知靠
用户级 wakeup stream。

## 7. 前端状态机

### 7.1 应用级生命周期

用户级 wakeup stream 绑定到当前浏览器标签页中的 MatMaster 已登录应用实例。同一用户开多个
标签页时，每个标签页各持有一条 wakeup stream，都订阅同一个
`chat:user:{user_id}:wakeup`。Redis pub/sub 会把 live wakeup 广播给该用户的所有标签
页，每个标签页在建连或重连时也各自收一遍 snapshot。这是预期行为：每个打开的页面都应独立
感知 session 唤醒。

启动条件：

```text
用户进入 MatMaster 应用范围
登录态有效
```

保持条件：

```text
用户在 MatMaster 页面之间切换
用户在不同 session 之间切换
```

关闭条件：

```text
用户登出
登录态失效且无法恢复
标签页关闭
离开 MatMaster 应用范围
```

网络异常、浏览器休眠或 SSE 断线不视为语义退出。前端应重连，重连后依赖 snapshot
恢复仍需关注的 session。

### 7.2 收到 wakeup

前端 reducer 只依赖 `session_id` 和 `reason`：

```text
on session_wakeup:
    if session_id == currentSessionId:
        ensureSessionStreamOpen(session_id)
    else:
        markSessionNeedsAttention(session_id)
```

`reason=trigger_enqueued`：

```text
可以标未读或轻提示
当前 session 确保打开 session stream
```

`reason=session_waiting_snapshot`：

```text
只恢复状态
不弹新通知
当前 session 确保打开 session stream
```

### 7.3 Session stream registry

前端维护 per-session stream registry：

```text
sessionStreamState[sessionId] =
  closed | connecting | open | closing
```

收到 wakeup 时：

```text
closed -> 打开 subscribe-only session stream
connecting/open -> 不重复打开
closing -> 等关闭完成后按需重开
```

由于后端同一 session 互斥运行，第一版不需要根据 task_id 区分并发 run。

### 7.4 聊天消息来源

wakeup stream 不插入聊天消息。

聊天 UI 只消费 session stream 里的事件。前端需要显式支持：

```text
source=System
type=trigger
```

`System/trigger` 是一轮后台触发的 turn 起点。它可以渲染成系统唤醒消息或后台触发
消息，但必须来自 session stream 的 history replay 或 live event，而不是 wakeup
payload。

## 8. 安全与权限

- Wakeup stream 只对登录用户开放。
- 不允许 share route 访问用户级 wakeup stream。
- 后端只发布到 session owner 对应的 `chat:user:{user_id}:wakeup` channel。
- 前端收到 wakeup 后如果要打开 session stream，仍走现有 session 权限校验。

## 9. 测试计划

### 9.1 Trigger publish 测试

覆盖成功路径：

```text
System/trigger 写入 history
job 成功入队
publish_user_wakeup 被调用一次
payload 只有 source/type/reason/session_id
reason == trigger_enqueued
```

覆盖负路径：

```text
deduped 不 publish
busy 不 publish
enqueue_failed 不 publish
session_not_found 不 publish
```

上述负路径分别对应 status 为 deduped、busy、error 的分支（enqueue_failed 与
session_not_found 都归 error），共同验证 6.1 的判定：只有 status==enqueued 才
publish。

### 9.2 Wakeup stream live 测试

模拟 Redis channel 收到：

```json
{
  "source": "System",
  "type": "session_wakeup",
  "reason": "trigger_enqueued",
  "session_id": "s1"
}
```

断言 SSE 输出为 ag-ui frame，且只转发当前登录用户的 wakeup。

### 9.3 Wakeup stream snapshot 测试

用户连接 wakeup stream 时，mock session service 返回该用户下 waiting / active
sessions：

```text
s1 waiting
s2 active
```

断言 stream 开头产生两个 snapshot wakeup：

```text
reason=session_waiting_snapshot
session_id=s1 / s2
```

断言 payload 不包含：

```text
task_id
invocation_id
status
origin
created_at_ms
content
```

### 9.4 内部 HTTP trigger 时序测试

新增或改造 internal trigger API 测试，断言：

```text
Redis session stream subscribe ready 之后才 lpush job
System/trigger 在入队前已经可 replay
返回 SSE 包含 System/trigger
```

该测试与普通发送路径的 subscribe-before-enqueue 不变量对齐。

### 9.5 前端测试建议

前端仓测试 reducer / hook：

```text
当前 session 收到 session_wakeup -> ensureSessionStreamOpen(session_id)
非当前 session 收到 session_wakeup -> markSessionNeedsAttention(session_id)
reason=session_waiting_snapshot -> 不弹 toast
reason=trigger_enqueued -> 可标未读或轻提示
重复 wakeup -> 不重复打开 stream
```

## 10. 验收标准

1. 后台 trigger 成功入队后，打开任意 MatMaster 页面都能收到 `session_wakeup`。
2. 当前会话页收到自己的 wakeup 后，会自动打开或重开 session `/stream`，并通过
   history replay 看到 `System/trigger`。
3. 非当前会话收到 wakeup 后，只更新会话列表、侧栏或提示，不打开该 session 的详情流。
4. 用户级 wakeup payload 严格保持四个字段：`source`、`type`、`reason`、`session_id`。
5. 用户级 wakeup payload 不包含 `task_id`、`invocation_id`、`status`、`origin`。
6. 内部 HTTP trigger 路径满足 subscribe-before-enqueue，不再存在先入队后订阅的竞态。

## 11. 实现注意事项

- 用户级 wakeup stream 是感知层，不是业务执行层；publish 失败只记录 warning。
- `publish_user_wakeup` 严禁下沉到共享的 `_enqueue_run`，否则普通前台发送也会误发
  wakeup；只允许出现在 `trigger_run` 与 `generate_internal_trigger_stream` 两处（见
  6.1 / 6.6）。
- snapshot 依赖新增 DAO 查询 `list_sessions_by_status(user_id, statuses)`；当前代码
  没有按 user_id 过滤 status 的现成方法（见 6.5）。
- `System/trigger` 仍是会话历史事件，不能被 wakeup payload 取代。
- 用户级 snapshot 只做当前状态恢复，不做通知历史回放。
- 第一版不新增 notification table。若后续要做站内通知中心，再单独设计持久通知模型。
- 不做兼容字段，不保留旧协议分支。
