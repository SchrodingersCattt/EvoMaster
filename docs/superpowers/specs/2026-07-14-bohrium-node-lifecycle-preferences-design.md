# Bohrium Node 生命周期偏好设计

## 目标

在保留“同一 user/org/project/SKU 共享一个 Node 槽位、每个 invocation 持有独立
lease”的基础上，让用户选择最后一个 invocation 结束后的 Node 行为，并同时提供持久设置
和逐轮确认两个入口。

## 用户策略

- `run_end`：最后一个 live lease 释放后立即 stop，槽位进入 `paused`。
- `idle_timeout`：最后一个 live lease 释放后保持运行，槽位进入 `idle`；只允许 900、1800、
  7200 秒，到期由 Worker monitor stop。
- `keep_running`：最后一个 live lease 释放后保持运行，槽位进入 `idle`，但
  `idle_expires_at` 为 NULL；MatMaster 不自动 stop。平台运维、异常和镜像替换仍可能停止 Node。

默认策略固定为 `run_end`。旧请求、旧偏好行和缺失字段都解析为该安全默认值。

## 偏好与逐轮快照

`matmaster-tools-server.user_preference` 保存：

- `bohrium_node_lifecycle_policy`
- `bohrium_node_idle_timeout_seconds`
- `bohrium_node_lifecycle_prompt_enabled`

设置页可修改默认策略以及“每次使用 Node 时询问”。Node-backed 发送前，如果询问开关开启，
前端弹出策略选择；“记住此选择，不再询问”会同时保存策略并关闭询问，否则只把本轮选择写入
请求。Sandbox 请求不展示该弹窗。

API 在入队时把 `policy + idle_timeout_seconds` 写入纯数据 job snapshot。Worker 不在运行时重新
读取用户偏好，因此用户随后修改设置不会改变已入队 invocation。非 Web 入口使用持久偏好；
读取失败或字段非法时回落 `run_end`。

## 槽位状态机

acquire 在 Redis 槽位锁内把本轮显式策略写成槽位的 latest desired policy。并发 invocation
共享 Node；最后一个 lease 释放时读取槽位当前策略：

- `run_end`：`ready -> stopping -> paused`
- `idle_timeout`：`ready -> idle`，写入绝对 `idle_expires_at`
- `keep_running`：`ready -> idle`，清空 `idle_expires_at`

新 acquire 可原子执行 `idle -> ready`、清理 deadline 并建立 lease。若 provider 实际已经停止，
沿用现有 restart/replace 流程。多个并发 invocation 选择不同策略时，槽位锁下最后成功 acquire
的显式策略生效；这是共享槽位成本控制的确定性规则。

monitor 扫描已到期的 `idle_timeout` 槽位，在槽位锁内重新检查 state、deadline 和 live lease，
CAS 到 `stopping` 后调用 provider。`keep_running` 的 NULL deadline 不进入扫描。Worker 崩溃导致
lease 过期时沿用同一 last-release 策略分派。

## 数据库与部署

evo 已有 state/lifecycle/idle 字段和到期索引，无需新增 evo DDL。tools-server 新增三个用户偏好
列，其中 prompt 默认 1；迁移必须先于依赖新列的 tools-server 版本。推荐部署顺序：

1. tools-server DMS migration；
2. tools-server；
3. evo API + Worker + monitor；
4. 前端。

协议字段均可选，旧前端继续得到 `run_end`。新前端在任一新接口暂时不可用时也必须安全回落
`run_end`，不能因为偏好服务故障阻断聊天。

## 测试重点

- 三种策略的严格组合校验以及 15m/30m/2h allowlist。
- 同槽位并发只创建一台 Node，非最后 lease 释放不改变状态。
- idle timeout 到期回收、新 acquire 取消 deadline、keep-running 不被 recycler 扫描。
- 请求字段完整贯穿 API、Redis job、Worker、Node acquire。
- 设置页持久保存、逐轮弹窗的单次/记住分支、请求体序列化和旧 API fallback。
