# Bohrium 历史 Node 运行态只读审计设计

## 背景与目标

`evo_bohrium_nodes` 升级为 invocation lease 状态机后，历史槽位会默认成为
`ready`，但新建的 `bohrium_node_leases` 为空。数据库状态不能证明平台上的 Node
仍在运行、已经 Paused 或已经不存在。

本次新增一个一次性、严格只读的命令行脚本，对 `ready + 无 live lease` 的历史槽位
查询 Bohrium 平台真实状态，并在终端输出保守的处理建议。脚本不停止 Node、不删除或
更新数据库，也不提供 `--apply` 模式。

## 命令与输出

脚本位置：`scripts/audit_bohrium_node_runtime.py`。

使用当前服务环境和数据库配置运行，例如：

```bash
SERVICE_ENV=test uv run python scripts/audit_bohrium_node_runtime.py --limit 1000
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

末尾输出总数和各 recommendation 数量。没有候选槽位时输出空结果摘要并以 0 退出。

## 数据流与边界

1. 通过只读 SQL 查询 `evo_bohrium_nodes`，并 LEFT JOIN `bohrium_node_leases`，只选择
   `state='ready'` 且不存在未过期 lease 的槽位。
2. 按 `user_id + org_id` 获取已有 Bohrium AccessKey。必须调用
   `UserService.get_existing_bohrium_access_key()`，不得自动创建 AK。
3. 调用只读 `node/list` 获取目标 Node 的原始详情，不调用 stop、restart、delete 或任何
   数据库写方法。
4. 终端和日志不得输出 AccessKey。审计路径复用的 `node/list` adapter 日志改为脱敏输出，
   但 HTTP 请求仍使用完整密钥。

脚本的核心分类函数与外部依赖分离，测试通过注入候选行、凭证读取器和 Node 详情读取器
验证行为；命令入口只负责组装现有 DAO/service。

## 建议分类

脚本不推断未验证的 Bohrium 状态码语义：

- 平台返回目标 Node 且 `status == 2`：`VERIFY_IDLE_THEN_STOP`。这只表示 Node 当前可用，
  仍需结合活跃任务确认后才能停止。
- `node/list` 中不存在目标 Node：`DB_ROW_STALE_CANDIDATE`。这是候选建议，不自动删 DB。
- 平台返回其他状态：`MANUAL_REVIEW_STATUS_<原始值>`；状态缺失时使用
  `MANUAL_REVIEW_STATUS_UNKNOWN`。
- 找不到已有 AK 或调用平台失败：`AUDIT_INCOMPLETE`，同时输出不含凭证的错误摘要。

只要存在 `AUDIT_INCOMPLETE`，进程以 2 退出；所有候选均完成平台查询时以 0 退出。数据库
连接或候选查询整体失败时以 1 退出。

## 安全与错误处理

- 代码中不提供 `--apply`，避免一次性审计脚本演变成无确认的批量清理工具。
- 不使用 `get_bohrium_access_key()`，防止审计行为替用户创建凭证。
- 不输出 Node 密码、AccessKey 或完整 HTTP header。
- 单个用户凭证或平台查询失败不阻断其他候选，最终用退出码 2 表示报告不完整。
- 数据库查询失败时不生成误导性的空报告，打印错误并以 1 退出。
- recommendation 只是人工复核输入，不能直接作为 stop/delete 的执行清单。

## 测试与验收

测试文件：`tests/scripts/test_audit_bohrium_node_runtime.py`。

覆盖：

- 只选择 ready 且无 live lease 的 SQL 契约；
- status=2、平台不存在、未知状态和缺失状态的保守分类；
- 缺少凭证和平台异常时继续其他行并返回不完整状态；
- 使用 existing-only AK loader；
- 输出包含审计字段和汇总，不包含 AccessKey；
- limit 校验和无候选结果；
- `node/list` adapter 日志只出现脱敏密钥。

验收命令：

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py \
  tests/services/test_bohrium_node_service.py -q
```
