# Bohrium stopping 槽位对账与启动诊断设计

## 背景

test 环境中 `evo_bohrium_nodes.id=182` 长期停留在 `stopping`，没有 live
invocation lease，所指向的 provider Node `20079897` 已不在当前用户的
`node/list` 中。monitor 仍重复调用 stop，并收到通用业务错误
`148888: rpc error`。与此同时，用户在 Bohrium 控制台手工创建的
`20079903` 独立停留在 provider `status=1`，它没有进入 MatMaster 数据库，
不属于本次自动对账的操作对象。

`148888` 是 Bohrium 的通用业务错误码，不能单独证明 Node 已停止或已删除。
对账必须以 provider 列表中的实际 Node 状态为依据。

## 方案比较

### 方案 A：只在 DMS 删除当前槽位

可以立即解除本次阻塞，但相同的 provider/DB 状态漂移会再次发生，不能作为长期方案。

### 方案 B：把 `148888` 或 `machine is not running` 视为 stop 成功

实现简单但不安全。同一错误码还会承载 `rpc error`、`record not found` 等不同语义；
Node 也可能仍处于 pending 或 stopping，不能直接发布为 paused。

### 方案 C：provider 状态对账并保留 fenced DB 转换（采用）

monitor 在重试 `stopping` 槽位前查询 provider `node/list`，根据可观测状态执行
幂等收敛。一次性 DMS 清理仍使用 slot/node/state/live-lease 围栏，仅用于恢复当前
test 数据。

## 运行时行为

`BohriumNodeLeaseManager.retry_stopping` 保留现有槽位锁、state 检查和 live lease
检查，然后读取 provider Node 详情：

- provider 列表不存在该 Node：按 user/org/project/SKU/node_id 围栏删除陈旧槽位；
- provider `status=-1`：按 slot/node/state 围栏将槽位转换为 `paused`；
- 其他状态：沿用现有 stop 重试；失败时保留 `stopping` 并记录 `last_error`。

不根据 `148888` 或错误消息直接改变数据库状态。provider 查询异常也保持
`stopping`，由后续 monitor tick 重试。

`acquire` 不直接抢占 `stopping` 槽位，避免与仍在进行的 provider stop 竞态；状态
收敛仍由单一 monitor 路径负责。

## 启动超时诊断

`BohriumNodeService.wait_until_ready` 在轮询期间只记录安全的最后观测值：

- 是否在 `node/list` 中找到；
- `status`；
- `startingUpMsg`；
- `errCode`。

超时异常包含这些字段，但不得包含 access key、密码、IP 或完整 provider 响应。
这样可以区分 provider 列表缺失、长期 pending 和显式启动失败。

## 一次性 test 数据恢复

代码验证完成后，对 `slot_id=182/node_id=20079897/state=stopping` 执行 fenced
DELETE。DELETE 必须同时确认不存在 `lease_expires_at > NOW()` 的 lease；执行后重新
查询槽位和 live lease 数量。该操作不调用 provider API，也不操作手工节点
`20079903`。

## 测试

- `retry_stopping` 在 provider missing 且无 live lease 时删除槽位且不再次 stop；
- provider `status=-1` 时转换为 paused；
- provider 查询异常或普通 stop 失败继续保留 stopping/last_error；
- live lease 或槽位状态变化时不得对账；
- `wait_until_ready` 超时包含最后 status/message/error code；
- `wait_until_ready` 成功路径保持现有返回契约。

## 非目标

- 不删除、停止或修改 Bohrium 控制台手工创建的 `20079903`；
- 不把 Sandbox `image_cache_status=1` 用作 readiness 门禁；
- 不改变 Node lifecycle 用户偏好、自动关机时间或数据库结构。
