# Bohrium Job ID Migration Design

## Context

当前 Bohrium builtin tool 在 submit 路径中同时处理 `job_id` 和
`bohr_job_id`：

- `BohriumTool._submit` 对模型返回 `job_id` 和 `bohr_job_id`。
- `BohriumTool._poll`、`_download`、`_kill` 的公开参数只接收 `job_id`。
- `JobRegistry`、`ChatEventsTable.get_bohrium_events()` 和 Bohrium job ledger
  设计都以 `job_id` 作为作业生命周期主标识。
- `get_file_token()` 的参数名是 `bohr_job_id`，但当前调用方实际传入的是
  canonical `job_id`。

这造成了两个问题：

- 对模型和脚本暴露两个相近 ID，容易让调用方误以为它们都是可操作的作业 ID。
- file-token / sandbox log 路径的参数名和实际调用语义不一致。如果 Bohrium
  sandbox file-token API 真的要求 `bohrJobId` 而不是 `jobId`，当前 live log 和
  sandbox log 预取可能在 `jobId != bohrJobId` 时静默失效。

本迁移设计只解决 ID 语义和工具协议边界，不实现 Bohrium job ledger，也不改变
API / Worker 分离架构。

## Goals

- 将 MatMaster 公开协议收敛为单一 canonical `job_id`。
- 删除模型可见 tool result content 中的顶层 `bohr_job_id`。
- 把 `job/create` 返回的 `jobId`、`job/add` 返回的 `jobId`、`bohrJobId` 三种
  平台字段区分成明确的内部语义。
- 修正 sandbox file-token 路径的命名，使参数名表达真实用途。
- 通过测试覆盖 `jobId != bohrJobId` 的分叉场景，避免再次把两个 ID 混用。
- 保持迁移式清理：不在主业务代码中加入旧字段兼容、自动迁移或运行时兜底。

## Non-Goals

- 不支持模型继续读取或传入 `bohr_job_id`。
- 不在 `Bohrium` tool schema 中新增 `bohr_job_id`、`file_token_job_id` 或其他第二
  作业 ID 参数。
- 不物理改写历史 `evo_chat_events` 对话审计日志。
- 不在主代码中加入从旧 `bohr_job_id` 自动推断 `job_id` 的兼容逻辑。
- 不把 `bohr_job_id` 提升为 `JobRegistry`、`SessionJobs` 或未来
  `bohrium_jobs` 表的主字段。

## Terminology

`create_job_id`

只表示 sandbox `job/create` 返回的 create/upload 句柄。它用于后续 `job/add`
请求，不代表已经提交成功的计算作业。它不得进入模型可见输出、`JobRegistry`、
`SessionJobs` 或 `bohrium_jobs.job_id`。

`job_id`

MatMaster canonical Bohrium 作业 ID。来源是 `job/add` 返回的 `jobId`。后续
`poll`、`download`、`kill`、`JobRegistry`、事件重建和 job ledger 都默认使用该
字段。

`file_token_job_id`

仅当 sandbox file-token API 经验证必须使用 `bohrJobId` 时才引入。它是内部窄字段，
只用于 `get_file_token()`，不是工具公开参数，也不是生命周期主键。如果 file-token
API 接受 canonical `job_id`，该字段可以与 `job_id` 相同，或不单独建模。

`raw_add_response`

`job/add` 成功后的原始平台响应快照。它可包含平台原始字段 `jobId` 和
`bohrJobId`，仅用于排障和外部迁移脚本，不参与常规业务判断。

## Contract Verification Gate

迁移前必须先验证 sandbox file-token API 对 ID 的真实要求。验证步骤：

1. 提交一个 sandbox job，记录 `job/add` 返回的 `jobId` 和 `bohrJobId`。
2. 调用 `GET /openapi/v1/sandbox/job/{jobId}`，确认状态查询使用 canonical
   `job_id`。
3. 调用 `POST /openapi/v1/sandbox/job/file/token` 两次：
   - payload `{"filePath": "log", "jobId": "<jobId>"}`
   - payload `{"filePath": "log", "jobId": "<bohrJobId>"}`
4. 将结果记录到短文档或实现计划的验证记录中。

这个 gate 决定后续实现路径：

- 如果 file-token 接受 `jobId`，实现走轻量路径：删除 `bohr_job_id`，并把
  `get_file_token()` 参数改名为 `job_id`。
- 如果 file-token 必须使用 `bohrJobId`，实现走内部映射路径：对外仍只暴露
  `job_id`，内部额外保存 `file_token_job_id`。

## Target Public Contract

`Bohrium(action="submit")` 成功后返回给模型的 JSON content：

```json
{
  "success": true,
  "job_id": "job-123",
  "status": "Submitted",
  "use_sandbox": true
}
```

`Bohrium(action="poll" | "download" | "kill")` 继续只接收 `job_id`：

```json
{
  "action": "poll",
  "job_id": "job-123"
}
```

任何调用方都不得通过 tool schema 或模型可见 result content 获取
`file_token_job_id`。如果内部需要该字段，应通过非模型协议传递，例如
`ToolResult.meta`、`ToolResult.payload`、`JobRegistry` 内部字段，或未来
`bohrium_jobs.submit_response_json`。

## Data Model Changes

在 `matmaster/tools/builtin/bohrium_tool/models.py` 中新增提交结果模型：

```python
@dataclass(frozen=True)
class BohriumSubmittedJob:
    job_id: str
    file_token_job_id: str
    raw_add_response: dict[str, Any]
```

约束：

- `job_id` 必须来自 `job/add` 返回的 `jobId`。
- `file_token_job_id` 的值由 contract verification gate 决定。
- `raw_add_response` 只保存 `job/add` 的响应，不保存 `job/create` 中包含上传 token
  的敏感字段。
- 对外 JSON content 不直接序列化 `raw_add_response`。

如果 contract verification gate 证明 file-token 也使用 `job_id`，实现计划可以选择
不新增 `file_token_job_id` 字段，但仍必须删除 `bohr_job_id` 的公开输出和命名。

## Submit Flow

目标流程：

```text
BohriumTool._submit
  -> submit_job_via_runtime(...)
       -> create_job(...)
       -> upload input archive
       -> add_job(...)
       -> build BohriumSubmittedJob
  -> return ToolResult(
       content={"success": true, "job_id": ..., "status": "Submitted", ...},
       meta/payload={internal identity data if needed}
     )
```

`submit_job_via_runtime()` 不再返回 `(job_id, bohr_job_id)` tuple。tuple 没有字段名，
容易让调用方把第二个值扩散到公共协议。

submit 失败时继续保持当前语义：

- 如果 `job/create` 成功但 upload 或 `job/add` 失败，计算作业没有提交成功。
- 错误信息可以包含非敏感 `created_job_ref`，但不把 create-only 记录写成已提交
  作业。

## Poll / Download / Kill Flow

`poll`、`download`、`kill` 的公开输入保持不变，只接收 canonical `job_id`。

`poll`

- 用 `job_id` 调 `get_job_detail()`。
- 如果 sandbox live log tail 需要 file-token，并且 contract gate 要求
  `file_token_job_id`，则从内部映射读取。
- 如果没有可用内部映射，poll 仍返回状态；live log tail 可以缺失，但不能把第二 ID
  暴露给模型让模型补传。

`download`

- 用 `job_id` 调 `get_job_detail()`。
- 结果 artifact 下载选择继续基于 `job_id` 和 `detail_data`。
- sandbox log prefetch 的 ID 选择由 contract gate 决定。
- 远端 helper payload 不接收长期 Bohrium access key，也不接收模型可见的第二 ID。

`kill`

- 只使用 `job_id`。
- 不引入 `file_token_job_id`。

## Registry And Event Semantics

`JobRegistry` 的 lifecycle 主键仍是 `job_id`。如果需要保存 file-token 内部映射，可以
在 `JobRecord` 中增加非主键字段：

```python
file_token_job_id: str = ""
```

`register()` 可以接收内部字段：

```python
def register(
    self,
    job_id: str,
    *,
    job_name: str = "",
    file_token_job_id: str = "",
) -> None:
    ...
```

`ChatEventsTable.get_bohrium_events()` 继续只从 tool result content 读取
`job_id`、`status`、`job_name` 和 `cached`。它不读取 `bohr_job_id`。

如果未来需要跨进程恢复 `file_token_job_id`，应由 job ledger 从
`submit_response_json` 或专门的内部字段恢复，不能从模型可见 content 恢复。

## Historical Data Migration

主代码不处理旧事件中的 `bohr_job_id`。

历史数据处理策略：

- 对话审计日志默认保留原样，因为它们记录的是历史事实。
- 测试 fixture 和开发库如果需要清理，由外部迁移脚本一次性处理。
- 外部迁移脚本可以放在 `scripts/migrations/`，由人工执行。
- 主业务代码不得包含读取旧 `bohr_job_id` 并自动补救的逻辑。

## Testing Strategy

必须新增或更新以下测试：

- submit 返回 `jobId != bohrJobId` 时，模型可见 content 只包含 `job_id`，不包含
  `bohr_job_id`。
- `poll`、`download`、`kill` 的公开参数仍只接受 `job_id`。
- `get_file_token()` 参数名与 contract gate 结论一致。
- sandbox live log tail 在 `jobId != bohrJobId` 时使用正确的 file-token ID。
- local download 的 sandbox log prefetch 使用正确的 file-token ID。
- remote download 的 sandbox log prefetch 使用正确的 file-token ID。
- `JobRegistry.rebuild_from_events()` 不依赖 `bohr_job_id`。
- `scripts/test_job_polling.sh` 只等待和使用 `job_id`。

建议验证命令：

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -q
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_download.py -q
uv run pytest tests/matmaster/bohrium/test_client.py -q
uv run pytest tests/matmaster/bohrium/test_artifacts.py -q
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py -q
uv run pytest tests/matmaster_bohrium_transfer/test_download.py -q
```

## Rollout Plan

1. 完成 sandbox file-token ID contract verification，并记录结论。
2. 引入 `BohriumSubmittedJob` 或等价的具名返回结构，替换 submit tuple。
3. 修改 `_submit()`，删除模型可见 content 中的 `bohr_job_id`。
4. 重命名 `get_file_token()` 参数，修正 poll/download/transfers 调用方。
5. 按需要扩展 `JobRegistry` 内部字段，但保持主键为 `job_id`。
6. 更新脚本和测试，不保留旧字段兼容。
7. 跑 focused pytest。
8. 做一次 repo-wide grep，确认 `bohr_job_id` 只存在于原始响应说明、迁移脚本或测试契约
   中。

## Acceptance Criteria

- `Bohrium(action="submit")` 的模型可见 content 不包含 `bohr_job_id`。
- `Bohrium` tool schema 不包含第二作业 ID 参数。
- `poll`、`download`、`kill` 全部以 `job_id` 为公开输入。
- `JobRegistry` 和事件重建仍以 `job_id` 为 lifecycle 主键。
- sandbox file-token 路径的参数命名与真实 API 契约一致。
- `jobId != bohrJobId` 的测试覆盖 poll log、local download log、remote download log
  三条路径。
- 主业务代码没有旧 `bohr_job_id` 自动兼容或迁移逻辑。
