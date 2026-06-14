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
publish session_wakeup 到用户级 channel
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

以下情况不发布 wakeup：

```text
deduped
busy
error
enqueue_failed
quota failed
session not found
```

这些情况没有产生新的可消费 run，前端不应被打扰。

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

1. 仅允许登录用户访问。
2. 不支持 share route。
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

每个 session 发一条：

```json
{
  "source": "System",
  "type": "session_wakeup",
  "reason": "session_waiting_snapshot",
  "session_id": "sess_xxx"
}
```

不从 chat history 或 notification history 回放 wakeup。第一版不新增 notification
table。

如果某个 run 已完成且 session 回到 idle，wakeup snapshot 不负责恢复它。用户进入该
session 后，由 session stream 的 history replay 展示完整历史。

### 6.6 内部 HTTP trigger 的时序修正

内部 HTTP trigger 不能继续先入队再订阅。它需要拆成 prepare 与 generator 两段：

```text
prepare_internal_trigger_run
  - 校验 owner / quota / Redis
  - 占用 session
  - 写 System/trigger
  - 组装 job
  - 不入队

generate_internal_trigger_stream
  - 推送 status + history + System/trigger
  - 建立 session Redis stream 订阅并等待 ready
  - _enqueue_run(job)
  - publish user wakeup
  - 转发 Worker live events
```

后台 monitor 直接调用 `trigger_run` 仍可同步入队。它没有浏览器 response，前端感知靠
用户级 wakeup stream。

## 7. 前端状态机

### 7.1 应用级生命周期

用户级 wakeup stream 绑定到当前浏览器标签页中的 MatMaster 已登录应用实例。

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
- `System/trigger` 仍是会话历史事件，不能被 wakeup payload 取代。
- 用户级 snapshot 只做当前状态恢复，不做通知历史回放。
- 第一版不新增 notification table。若后续要做站内通知中心，再单独设计持久通知模型。
- 不做兼容字段，不保留旧协议分支。
