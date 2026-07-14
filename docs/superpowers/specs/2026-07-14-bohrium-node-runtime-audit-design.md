# Bohrium 历史 Node 运行态审计与批量停止设计

## 背景与目标

`evo_bohrium_nodes` 升级为 invocation lease 状态机后，历史槽位会默认成为
`ready`，但新建的 `bohrium_node_leases` 为空。数据库状态不能证明平台上的 Node
仍在运行、已经 Paused 或已经不存在。

本次新增一个一次性命令行脚本，对 `ready + 无 live lease` 的历史槽位查询 Bohrium
平台真实状态，并在终端输出保守建议。默认模式严格只读；操作者同时传入 `--apply` 和
`--confirm-stop-all-unleased-ready` 后，脚本自动停止本轮全部 `status=2` 候选并把槽位
推进到 `paused`。

## 命令与输出

脚本位置：`scripts/audit_bohrium_node_runtime.py`。

使用当前服务环境和数据库配置运行，例如：

```bash
SERVICE_ENV=test uv run python scripts/audit_bohrium_node_runtime.py --limit 1000
```

审阅 dry-run 后执行全部可停止候选：

```bash
SERVICE_ENV=test uv run python scripts/audit_bohrium_node_runtime.py \
  --limit 1000 \
  --apply \
  --confirm-stop-all-unleased-ready
```

`--limit` 必须为正整数，默认值和最大值均为 1000。查询按 `last_used_at` 从旧到新排序。
终端逐行输出以下字段：

- `node_id`
- `user_id`
- `org_id`
- `project_id`
- `sku_id`
- `last_used_at`
- Bohrium 返回的原始 `status`
- Bohrium 返回的镜像名
- 保守的 `recommendation`
- 本轮 `execution`

末尾输出总数和各 recommendation 数量。没有候选槽位时输出空结果摘要并以 0 退出。

## 数据流与边界

1. 通过只读 SQL 查询 `evo_bohrium_nodes`，并 LEFT JOIN `bohrium_node_leases`，只选择
   `state='ready'` 且不存在未过期 lease 的槽位。
2. 按 `user_id + org_id` 获取已有 Bohrium AccessKey。必须调用
   `UserService.get_existing_bohrium_access_key()`，不得自动创建 AK。
3. 调用只读 `node/list` 获取目标 Node 的原始详情。dry-run 到此结束，不调用任何写方法。
4. 终端和日志不得输出 AccessKey。审计路径复用的 `node/list` adapter 日志改为脱敏输出，
   但 HTTP 请求仍使用完整密钥。
5. apply 模式只对 `status=2` 候选调用 lifecycle manager。manager 在现有 Redis 槽位锁下
   重新读取槽位，确认 `state='ready'`、`node_id` 未变化；随后用带到期条件的 DELETE 原子
   退休过期 lease，再确认没有 live lease，最后原子切换为 `stopping`。这样并发 heartbeat
   要么先续期并被 live 检查拦截，要么在过期行删除后无法复活。provider stop 在锁外执行，
   成功后重新加锁切换为 `paused`。
6. stop 失败时保留 `stopping` 并写入 `last_error`，由现有 monitor recycler 重试；竞态检查
   失败则跳过，不调用 provider。

脚本的核心分类函数与外部依赖分离，测试通过注入候选行、凭证读取器和 Node 详情读取器
验证行为；命令入口只负责组装现有 DAO/service。AccessKey 只作为函数调用的临时参数，
不会进入报告对象。

## 建议分类

脚本不推断未验证的 Bohrium 状态码语义：

- 平台返回目标 Node 且 `status == 2`：`VERIFY_IDLE_THEN_STOP`。dry-run 只展示建议；apply
  模式对全部此类候选执行安全重检和 stop。
- `node/list` 中不存在目标 Node：`DB_ROW_STALE_CANDIDATE`。这是候选建议，不自动删 DB。
- 平台返回其他状态：`MANUAL_REVIEW_STATUS_<原始值>`；状态缺失时使用
  `MANUAL_REVIEW_STATUS_UNKNOWN`。
- 找不到已有 AK 或调用平台失败：`AUDIT_INCOMPLETE`，同时输出不含凭证的错误摘要。

`execution` 使用 `DRY_RUN`、`STOPPED_TO_PAUSED`、`SKIPPED_SLOT_CHANGED`、
`SKIPPED_CONCURRENT_LEASE`、`PROVIDER_MISSING_SLOT_REMOVED`、
`PROVIDER_MISSING_SLOT_ALREADY_ABSENT`、`NOT_ELIGIBLE` 或 `FAILED_<异常类型>`。只要发生
apply 执行失败，进程以 3 退出；否则存在 `AUDIT_INCOMPLETE` 时以 2 退出；生产依赖初始化、
数据库连接或候选查询整体失败时以 1 退出；其余情况以 0 退出。

## 安全与错误处理

- 默认模式严格只读。`--apply` 缺少 `--confirm-stop-all-unleased-ready` 时拒绝执行；确认参数
  单独出现时同样拒绝，避免含糊的命令行。
- 不使用 `get_bohrium_access_key()`，防止审计行为替用户创建凭证。
- 不输出 Node 密码、AccessKey 或完整 HTTP header。
- 单个用户凭证或平台查询失败不阻断其他候选，最终用退出码 2 表示报告不完整。
- 数据库查询失败时不生成误导性的空报告，打印错误并以 1 退出。
- 初次 `node/list` 已不存在或状态不是 2 的记录不自动修改，仍作为人工复核输入。
- apply 过程中若 provider 报 Node 不存在，重新获取槽位锁，并用
  `slot_id + node_id + state='stopping'` 精确 CAS 删除；槽位已不存在和槽位已变化分别输出
  `PROVIDER_MISSING_SLOT_ALREADY_ABSENT` 与 `SKIPPED_SLOT_CHANGED`。其他 stop 异常不得把
  槽位标为 paused。
- existing-AK 和 `node/list` 的异常日志只记录异常类型，不记录可能携带密钥的异常正文。
- 历史任务没有 invocation lease，脚本无法证明它们是否仍在使用 Node。确认参数明确表示
  操作者接受“全部 `status=2 + 无新 lease` 历史 Node 均可停止”这一迁移风险。

## 测试与验收

测试文件：`tests/scripts/test_audit_bohrium_node_runtime.py`。

覆盖：

- 只选择 ready 且无 live lease 的 SQL 契约；
- status=2、平台不存在、未知状态和缺失状态的保守分类；
- 缺少凭证和平台异常时继续其他行并返回不完整状态；
- 使用 existing-only AK loader；
- 输出包含审计字段和汇总，不包含 AccessKey；
- limit 校验和无候选结果；
- apply 缺少二次确认时拒绝执行；
- apply 只处理 status=2，未知/缺失状态不调用 stop；
- 槽位改变或出现并发 lease 时跳过；
- 过期 lease 与 heartbeat 竞态时，续期成功必须跳过，退休成功后 lease 不得复活；
- stop 成功进入 paused，超时保留 stopping/error，404 对账删除不存在槽位；
- apply 失败优先返回退出码 3；
- `node/list` adapter 日志只出现脱敏密钥。

验收命令：

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py \
  tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_service.py -q
```
