# Bohrium SDK-Free Large File Transfer Design

## 背景

当前 builtin `Bohrium` 工具支持 `submit`、`poll`、`download`、
`kill`、`list_images`、`list_machines` 等操作。其中 `submit` 与
`download` 直接承担材料计算输入和结果的大文件传输。

现有实现已经把 Bohrium 控制面 API 从 `bohrium-sdk` 中抽出：

- `matmaster/bohrium/client.py` 通过 `requests` 调用 `job/create`、
  `job/add`、`job/detail`、sandbox file token、image/machine 查询等 API。
- `matmaster/bohrium/artifacts.py` 通过 `requests` 下载结果 URL 或
  sandbox 对象列表。

仍然依赖 `bohrium-sdk` 的核心路径是上传：

- `matmaster/bohrium/upload.py` 通过
  `bohrium.resources.tiefblue.Tiefblue.upload_From_file_multi_part()` 上传
  本地 `input.zip`。
- `matmaster/bohrium/remote_transfer_helper.py` 在远端 Bohrium 节点上也
  import `Tiefblue` 上传远端打包出的 `input.zip`。
- `pyproject.toml` 与 `Dockerfile.remote` 因此保留
  `bohrium-sdk>=0.15.0`。

`bohrium-sdk` 的 Tiefblue 实现不适合当前材料计算的大文件传输场景：

- 小文件上传路径会调用 `fp.read()`，把文件整体读入内存。
- multipart 上传按 part 串行执行，不支持可控并发。
- 错误分类、manifest 恢复、进度事件、远端版本治理都不是当前项目
  能控制的边界。

本设计将 builtin `Bohrium(action="submit")` 与
`Bohrium(action="download")` 的数据面迁移到项目自研传输组件。控制面
仍由主项目负责。

## 目标

- 最终状态去掉 builtin Bohrium submit/download 数据面对 `bohrium-sdk` 的依赖。
  分阶段交付期间允许 legacy path 暂时保留，直到 Phase D 移除。
- 支持 5GB 到 100GB 级别的材料计算输入和结果传输。
- 上传和下载都必须流式处理，内存占用不能随文件大小线性增长。
- 上传支持可配置并发 multipart，默认保守参数：
  - `part_size = 64MB`
  - `upload_concurrency = 4`
  - `download_concurrency = 4` when HTTP Range is supported
  - `part_retries = 3`
- 支持同一会话内的 manifest 恢复。Worker 重启后，只要 session
  workspace 或远端 manifest 仍存在，就可尽量继续传输。
- 本地路径和远端 `/share/...`、`/personal/...` 路径使用同一套传输语义。
- 远端不再运行时复制 helper 源码。远端镜像预装专有 transfer package。
- 当前版本只做日志、manifest 与最终 summary，但传输核心预留
  `ProgressSink`，便于后续接入 SSE/Redis 实时进度基础设施。
- 保持现有 Bohrium submit 的 public tool response 字段尽量稳定。

## 非目标

- 不实现平台级后台 transfer task、DB 状态、暂停、取消或前端进度条。
- 不默认切换到 `tar` 或 `tar.gz` 作为 submit 输入格式。
- 不跨会话、跨用户、跨 project 复用 manifest。
- 不改 MCP calculation path adaptor、产品 OSS、workspace archive、
  response figures 的存储链路。
- 不引入远端额外依赖，如 `httpx`、`zstandard`、`xxhash`。
- 不保留运行时复制 helper 源码的 fallback。

## 已确认设计决策

### 范围

第一版覆盖 builtin `Bohrium` 的 `submit` 与 `download` 两条链路。
不覆盖 MCP calculation path adaptor、产品侧 OSS 上传、workspace 归档或
response figures。

`matmaster/tools/builtin/bohrium_tool/paths.py` 与 `models.py` 保留在主项目。
它们属于控制面的路径解析与 tool 参数建模，不进入独立 transfer package。

### 协议层

第一版直接按当前 `Tiefblue` 行为重写 storeHost HTTP 客户端，不等待正式
Bohrium storage 契约。需要固化的 HTTP 行为包括：

- 小文件上传兼容接口：`POST /api/upload/binary`
- multipart init：`POST /api/upload/multipart/init`
- multipart part upload：`POST /api/upload/multipart/upload`
- multipart complete：`POST /api/upload/multipart/complete`
- 下载：`GET /api/download/{object_key}?token=...`
- sandbox list：`POST /api/iterate`

`Tiefblue` 本身不限制 zip、tar 或 gz。它只是对象存储客户端。真正决定
归档如何进入作业工作目录的是 Bohrium `job/add` 的输入文件模式。

### 归档格式

submit 默认仍生成 `input.zip`，以保持当前 Bohrium 自动解包路径的兼容性。
但 zip 内部默认使用 `ZIP_STORED`，也就是 zip 容器、不压缩。

原因：

- 仍然是合法 zip，兼容性接近当前实现。
- 避免对 5GB 到 100GB 的大文件目录做高 CPU 成本 deflate 压缩。
- 单一稳定归档文件适合 multipart checkpoint，part offset 稳定。
- `tar` / `tar.gz` 支持情况需要真实 Bohrium 环境验证后再考虑切换默认值。

空 `input_dir` 语义保持现状：允许生成合法的空 `input.zip` 并提交。该行为
用于兼容已有测试与调用方，不在第一版中改为 reject。

### Archive Fingerprint

目录 fingerprint 不对整个 `input.zip` 做全量 SHA-256。100GB 级归档每次
resume 都全量读 archive 会把恢复成本推高到不可接受。

submit 输入目录 fingerprint 使用便宜的结构摘要：

```text
sha256(
  json.dumps(
    sorted([
      {
        "rel_path": rel_path_posix,
        "size": file_size,
        "mtime_ns": file_mtime_ns,
        "mode": file_mode & 0o777,
        "kind": "file" | "symlink"
      },
      ...
    ]),
    separators=(",", ":")
  )
)
```

该 fingerprint 用于识别输入目录是否被替换、增删或修改。archive 文件本身
仅校验 `archive_size`、`archive_mtime_ns`、`archive_format`、
`archive_compression` 与 `source_fingerprint`。严格内容 SHA-256 可作为
以后可选的慢校验模式，但不是 v1 默认行为。

### 远端安装与调用

远端 Bohrium 镜像不安装完整 `matmaster` 项目，只安装一个专有轻量包：

```text
matmaster_bohrium_transfer
```

该包由主仓库内的独立 package 产出：

```text
packages/bohrium-transfer/
  pyproject.toml
  src/matmaster_bohrium_transfer/
    __init__.py
    archive.py
    client.py
    download.py
    errors.py
    manifest.py
    multipart.py
    progress.py
    remote.py
    version.py
```

远端执行入口：

```bash
python -m matmaster_bohrium_transfer.remote version --json
python -m matmaster_bohrium_transfer.remote upload-submit --payload-file <payload.json>
python -m matmaster_bohrium_transfer.remote download-results --payload-file <payload.json>
```

Worker 在远端传输前先执行版本探测。远端缺失 package、协议版本不兼容或
capability 不足时，直接失败并提示更新远端镜像或重启 Bohrium session。
不做源码复制 fallback。

## 架构

主项目仍负责控制面：

```text
matmaster-evo
  -> matmaster.tools.builtin.bohrium_tool.tool.BohriumTool
      -> resolve input/result paths
      -> build Bohrium context
      -> job/create, job/add, job/detail
      -> local transfer package call or remote transfer CLI call
      -> ToolResult summary
```

独立传输包负责数据面：

```text
matmaster_bohrium_transfer
  archive.py
    input_dir -> ZIP_STORED input.zip
    archive fingerprint
    safe extraction helpers

  client.py
    storeHost HTTP protocol
    Tiefblue-compatible headers
    download / iterate helpers

  multipart.py
    concurrent multipart upload
    part retry
    manifest resume
    complete ordering

  manifest.py
    sensitive manifest read/write
    0600 file permissions
    lock management
    fingerprint validation

  download.py
    Range probing
    .part resume
    streaming download
    staging extraction
    atomic publish

  progress.py
    ProgressSink
    progress event schema
    logging and manifest sinks

  remote.py
    JSON CLI for remote node execution

  version.py
    package version
    protocol version
    capabilities
```

## Version 与 Capability 契约

独立包暴露四类版本或能力信息，它们各自负责不同边界：

- `package_version`：Python package 版本，用于发布追踪和日志排查。
- `protocol_version`：Worker 与远端 CLI 的交互协议版本，使用
  `major.minor`。相同 major 下 minor 只能做向后兼容的字段新增；major
  变化视为不兼容。
- `schema_version`：manifest 与 payload 的数据结构版本。读取旧 schema
  时必须显式迁移或拒绝，不能静默忽略未知关键字段。
- `capabilities`：远端实际支持的能力列表。Worker 不只看版本号，还必须检查
  当前操作需要的 capability。

v1 capabilities 至少包括：

```text
multipart_upload
upload_concurrency
manifest_resume
range_resume
range_download_concurrency
sandbox_iterate
zip_stored
secure_payload_file
redacted_errors
```

Worker 远端执行前必须检查：

- `protocol_version` major 与 Worker 兼容。
- 所需 capabilities 均存在。
- `package_version` 与 `git_commit` 进入日志和错误上下文，但默认不要求与
  Worker commit 完全相同。

## 远端运行环境契约

远端镜像必须提供可执行 Python runtime 与已安装的
`matmaster_bohrium_transfer` package。

Python binary discovery：

- 优先读取 Worker 配置或环境变量 `BOHRIUM_TRANSFER_REMOTE_PYTHON`。
- 未配置时默认使用 `python3`。
- 远端 Python 版本要求 `>=3.11`。

远端 verify 命令：

```bash
<python> -m matmaster_bohrium_transfer.remote version --json
```

该命令必须返回 JSON，包含 `ok`、`package_version`、`protocol_version`、
`schema_version`、`git_commit`、`capabilities`、`python_version`。

远端 entrypoint 使用 `python -m matmaster_bohrium_transfer.remote`，不依赖
console_scripts 是否进入 PATH。

## Submit 数据流

### 本地输入目录

```text
BohriumTool._submit
  -> build_bohrium_context(require_project=True)
  -> resolve_input_source(local input_dir)
  -> create_job(ctx, job_name)
  -> matmaster_bohrium_transfer.archive.create_zip_store(input_dir)
       writes input.zip with ZIP_STORED
       records archive fingerprint
  -> matmaster_bohrium_transfer.multipart.upload_file(...)
       validate or create sensitive manifest
       init multipart if no reusable upload exists
       upload parts concurrently
       retry failed parts
       persist completed partString values
       complete multipart
  -> add_job(ctx, create_data, upload, ...)
  -> return ToolResult with final transfer summary
```

`job/add` 只在上传成功后调用。

### 上传 retry 与退避

`part_retries = 3` 是每个 part 的最大尝试次数，不是整个 upload 的总次数。

可重试错误：

- HTTP 429。
- HTTP 5xx。
- connection reset、timeout、temporary DNS failure 等网络瞬断。

不可重试错误：

- HTTP 400、401、403、404，除非明确被分类为 token refresh 可恢复。
- manifest schema 不兼容。
- source fingerprint 不匹配。

退避策略：

```text
sleep = min(base_delay * 2 ** (attempt - 1), max_delay) + jitter
base_delay = 1s
max_delay = 30s
jitter = random 0-1s
```

当同一时间多个 part 失败且状态码为 429 或 5xx 时，上传器需要做全局退避，
避免 `upload_concurrency * part_retries` 同时压向 storeHost。

### 远端输入目录

```text
BohriumTool._submit
  -> build_bohrium_context(require_project=True)
  -> resolve_input_source(remote /share/... or /personal/...)
  -> create_job(ctx, job_name)
  -> remote version probe
  -> secure-write payload.json to remote temp path with mode 0600
  -> ssh/session exec:
       python -m matmaster_bohrium_transfer.remote upload-submit --payload-file ...
  -> remote package creates ZIP_STORED input.zip in session transfer workspace
  -> remote package uploads directly to storeHost
  -> remote CLI returns non-sensitive JSON summary
  -> Worker calls add_job(ctx, create_data, upload, ...)
```

远端大文件数据面不经过 Worker。Worker 只传递小 JSON payload，并读取远端
CLI 的非敏感 JSON stdout。

### 上传失败

远端或本地上传失败后：

- 不调用 `job/add`。
- 返回明确错误：compute job was not submitted。
- 错误里可包含非敏感 `created_job_ref`。
- 不暴露 token、access key、带 token 的 download URL。
- create-only job 记录可能留在 Bohrium 侧，这是第一版接受的控制面残留。

### Token TTL 与 create-only job 风险

manifest 允许保存 token 是为了支持同会话恢复，但 v1 不假设 storeHost 一定
支持在同一个 multipart `initial_key` 上无损切换新 token。

manifest 必须记录：

- `token_obtained_at`
- `token_expires_at` when known
- `token_ttl_seconds` when known
- `estimated_upload_seconds` when available

实现需要通过真实 storeHost contract test 或配置确认 token TTL。若 token TTL
已知且预计上传时间超过 TTL，默认应在上传前失败并给出可操作错误，而不是开始
一个大概率会过期的 100GB 上传。若 token TTL 未知，summary 与日志中必须标记
`token_ttl_unknown=true`，并且只承诺 token 有效期内的 manifest resume。

token 过期后的恢复策略：

- 如果 storeHost 允许同一 `initial_key` 使用新 token 继续上传，传输组件可以
  保留 part 状态并刷新 token。
- 如果不允许，manifest 必须标记为不可恢复，Worker 重新 `job/create` 并从头
  上传。

该限制必须写入 user-visible safe error，避免让用户误以为所有跨小时失败都能
无损续传。

## Download 数据流

```text
BohriumTool._download
  -> build_bohrium_context()
  -> get_job_detail(ctx, job_id)
  -> reject running status
  -> require success or failure terminal status
  -> choose local or remote result target
```

### 本地结果目录

```text
detail_data / resultUrl / sandbox objects
  -> transfer.download.probe_range()
  -> split into byte ranges when Range is supported
  -> download ranges concurrently into .part file
  -> resume completed ranges from manifest
  -> atomic rename to complete archive
  -> extract to staging dir
  -> atomic publish to result_dir
  -> return files, log_tail, transfer summary
```

### 远端结果目录

```text
Worker fetches job detail and sandbox log token when needed
  -> remote version probe
  -> secure-write payload.json to remote temp path with mode 0600
  -> ssh/session exec:
       python -m matmaster_bohrium_transfer.remote download-results --payload-file ...
  -> remote package downloads directly from resultUrl/storeHost
  -> Range resume and concurrent range download when supported
  -> extract to remote staging dir
  -> atomic publish to /share/... result_dir
  -> remote CLI returns non-sensitive JSON summary
```

### Range 能力

下载前探测：

- `Content-Length`
- `Accept-Ranges`
- `Range: bytes=0-0`

支持 Range 时：

- 写入 `.part` 文件。
- 将目标对象拆为 range parts，默认 `download_concurrency = 4`。
- manifest 复用 `parts` 结构记录 download part 的 offset、size、状态。
- manifest 记录 URL 指纹、目标路径、已下载字节数、总大小、ETag、
  Last-Modified 等可用信息。
- 重试时跳过已完成 range parts，继续未完成 range parts。
- part 写入必须使用 offset-aware writes，避免多个线程互相覆盖。

不支持 Range 时：

- 仍然流式下载，不整体进内存。
- manifest 记录 `resume_supported=false`。
- 失败后只能从头下载。

如果响应缺少 `Content-Length`，或使用 chunked encoding 导致总大小未知：

- `resume_supported=false`。
- 进度事件中的 `bytes_total=null`。
- 下载仍然流式执行，但不能宣称支持 resume。

### Sandbox 下载 fallback 链

新的 transfer package 必须保留当前 `matmaster/bohrium/artifacts.py` 的 sandbox
下载语义，不能退化为只下载单一 `resultUrl`。

sandbox result download 顺序：

1. 解析 `resultUrl`，通过 `/api/iterate` 列出 root prefix 下对象。
2. 独立尝试 sandbox log token：
   - Worker 调 `/openapi/v1/sandbox/job/file/token` 获取 `log` token。
   - 远端 download payload 只携带该文件的临时 token，不携带长期 access key。
3. 优先选择文件名为 `{job_id}.zip` 的 zip 对象。
4. 若不存在 `{job_id}.zip`，选择任意非 `task.zip` 的 zip 对象。
5. 若仍不存在，选择任意 zip 对象。
6. 若可用 zip 下载或解压失败，尝试直接把 `resultUrl` 当 zip 下载。
7. 若 zip 路径都失败，退化为 iterate + 单对象下载，保留当前
   `_SANDBOX_OBJECT_DOWNLOAD_LIMIT = 128` 语义，跳过目录对象和 zip 对象。
8. 如果只成功拿到 log，也返回 `files=["log"]` 与 log tail。

该 fallback 链是兼容性要求。任何重构都必须有主项目集成测试覆盖这些分支，
防止 sandbox 任务结果下载回归。

## Manifest 与恢复

manifest 是会话级敏感临时状态，不是平台级任务记录。

本地位置：

```text
.matmaster/transfers/{transfer_id}/manifest.json
```

远端位置：

```text
/share/.matmaster/transfers/{transfer_id}/manifest.json
/personal/.matmaster/transfers/{transfer_id}/manifest.json
```

manifest 记录：

- `schema_version`
- `transfer_id`
- `direction`: `upload` 或 `download`
- `operation`: `submit_input` 或 `job_result`
- `store_host`
- `store_path`
- `object_key`
- `token`
- `initial_key`
- `token_obtained_at`
- `token_expires_at`
- `token_ttl_seconds`
- `part_size`
- `concurrency`
- `file_size`
- `file_mtime_ns`
- `source_fingerprint`
- `archive_size`
- `archive_mtime_ns`
- `archive_format`
- `archive_compression`
- `parts`
- `resume_supported`
- `created_at`
- `updated_at`
- `last_error_stage`
- `package_version`
- `protocol_version`

恢复前必须校验：

- manifest schema 版本。
- transfer protocol 版本。
- storeHost / objectKey / operation。
- 文件大小与 mtime。
- archive strategy。
- part size 与 concurrency 兼容性。
- URL 指纹、Content-Length、ETag、Last-Modified 等下载端元数据。

校验失败则废弃 manifest，从头开始。

上传恢复时，如果 token、`initial_key` 或已上传 part 状态失效，或者
storeHost 返回 401、403、404，传输组件废弃当前 manifest。对于 submit，
Worker 需要重新 `job/create` 并从头上传。

下载恢复时，如果 URL 指纹、`Content-Length`、ETag 或 Last-Modified
不匹配，废弃 `.part` 与 manifest，从头下载。

### Manifest 清理与 GC

manifest 与 `.part` 文件不能无限期留在 `/share/.matmaster/transfers`。

清理规则：

- 传输成功后，删除 payload 文件、临时 lock、无用 `.part` 文件。
- 成功的 upload manifest 可保留一个短窗口用于审计和 retry summary，默认
  不超过 24 小时；若实现不需要保留，应成功后立即删除。
- 失败的 manifest 默认保留 7 天，用于同会话恢复和排查。
- 每次启动 transfer 操作前，transfer package 对当前 manifest root 执行轻量
  GC，删除 mtime 超过 7 天且没有 lock 的失败或临时目录。
- GC 不删除正在持有 lock 的目录。
- GC 行为必须记录脱敏日志。

## 安全

- manifest 目录尽量使用 `0700`。
- manifest 文件必须使用 `0600`。
- payload 文件必须使用 `0600`。
- payload 文件读取后尽量删除。
- payload 与 manifest 写入必须原子创建。实现应使用
  `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)` 或等价机制，
  不能依赖先写入再 `chmod 600` 的事后修正。
- token、access key、带 token 的 URL 可存在于 manifest 和 payload，但不能进入：
  - 普通日志
  - tool result
  - remote CLI stdout
  - user-visible error
- 所有错误输出经过统一 `redact_secrets()`。
- 远端 CLI stdout 只输出非敏感 JSON summary。
- 若 remote CLI 返回失败，返回 `safe_message`、`stage`、`retryable`、
  `resume_available` 等字段，不返回敏感 payload。

`redact_secrets()` 的契约：

- redact query string 中的 `token`、`access_key`、`accessKey`。
- redact `Authorization` header 与 `Bearer ...`。
- redact JSON object 中 key 名匹配 `token`、`access_key`、`accessKey`、
  `authorization` 的 value。
- redact URL path segment 中明显的 token-like 长随机串，至少覆盖长度超过
  24 且只包含 URL-safe token 字符的 segment。
- redaction 应用于 exception message、HTTP response body、remote stdout/stderr
  解析失败文本、manifest validation error。

## 错误处理

传输包提供阶段化错误类型：

```text
ArchiveError
StorageInitError
StoragePartUploadError
StorageCompleteError
ManifestError
ResumeValidationError
RangeProbeError
DownloadError
ExtractError
PublishError
RemoteVersionError
```

每个错误包含：

- `stage`
- `retryable`
- `safe_message`
- `transfer_id`
- `bytes_done`
- `bytes_total`
- `resume_available`
- `redacted_detail`

`BohriumTool` 只使用 `safe_message` 组装 tool result。完整 traceback 仅进入
后端日志，且必须脱敏。

远端 CLI 失败 JSON 形态：

```json
{
  "schema_version": "v1",
  "protocol_version": "1.0",
  "ok": false,
  "stage": "part_upload",
  "retryable": true,
  "safe_message": "remote upload failed during part upload; retry can resume",
  "transfer_id": "submit-input-...",
  "resume_available": true
}
```

### Remote CLI exit code 协议

Worker 必须区分远端进程的三种结果，不能只按 exit code 简化处理：

| exit code | stdout | 含义 | Worker 行为 |
|-----------|--------|------|-------------|
| `0` | JSON `ok=true` | 操作成功 | 解析 summary，继续控制面流程 |
| non-zero | JSON `ok=false` | 业务失败或可分类传输失败 | 使用 `stage`、`retryable`、`safe_message` 生成 tool error |
| non-zero | 非 JSON 或空 | 进程崩溃、OOM、Python import error、被 kill | 生成 `RemoteExecutionError`，附脱敏 stdout/stderr 摘要 |

如果 exit code 为 `0` 但 JSON 缺失、不是 object、`schema_version` 或
`protocol_version` 不兼容，Worker 必须按协议错误处理，不能视为成功。

## 进度扩展

第一版不接实时 SSE，但传输核心提供可选进度接口：

```text
ProgressSink.emit(event: TransferProgressEvent) -> None
```

第一版实现：

- `NoopProgressSink`
- `LoggingProgressSink`
- `ManifestProgressSink`

事件类型：

```text
transfer_started
archive_created
upload_part_completed
download_chunk_completed
transfer_resumed
transfer_completed
transfer_failed
```

事件字段：

- `transfer_id`
- `phase`
- `direction`
- `bytes_done`
- `bytes_total`
- `parts_done`
- `parts_total`
- `rate_mbps`
- `resume_supported`
- `location`
- `package_version`
- `protocol_version`

事件发射必须限频：

- `upload_part_completed` 按 part 完成发射；默认 part size 为 64MB。
- `download_chunk_completed` 不能按底层 `iter_content()` 的小 chunk 逐个发射。
- 下载进度事件默认按以下任一条件聚合：
  - 累计字节变化达到 32MB。
  - 距离上次发射至少 1 秒且有进度变化。
  - 一个 range part 完成。
- 当未来实现并发 Range 下载时，推荐将用户可见事件命名为
  `download_part_completed`，`download_chunk_completed` 仅作为内部事件或
  聚合后事件。

后续接入 SSE/Redis 进度基础设施时，只需新增对应 `ProgressSink` 实现，
不改变传输核心。

## 发布链路

新增独立 package：

```text
packages/bohrium-transfer/
```

新增构建脚本：

```text
scripts/build_bohrium_transfer_bundle.py
```

构建脚本负责：

- 构建 wheel 或 sdist。
- 写出 bundle metadata：
  - package version
  - protocol version
  - git commit
  - built at
  - sha256
  - capabilities
- 输出 `.sha256` 文件。

`Dockerfile.remote` 通过 OSS URL 安装专有包：

```dockerfile
ARG MATMASTER_BOHRIUM_TRANSFER_URL=""
ARG MATMASTER_BOHRIUM_TRANSFER_SHA256=""

RUN if [ -n "$MATMASTER_BOHRIUM_TRANSFER_URL" ]; then \
      wget -O /tmp/matmaster_bohrium_transfer.whl "$MATMASTER_BOHRIUM_TRANSFER_URL" && \
      echo "$MATMASTER_BOHRIUM_TRANSFER_SHA256  /tmp/matmaster_bohrium_transfer.whl" | sha256sum -c - && \
      pip install --no-cache-dir /tmp/matmaster_bohrium_transfer.whl && \
      rm -f /tmp/matmaster_bohrium_transfer.whl ; \
    fi
```

生产远端镜像推荐从 OSS 安装固定 wheel。开发环境可以通过本地 wheel 或
editable install 安装。

`Dockerfile.remote` 移除 `bohrium-sdk>=0.15.0`。远端运行时只需要 Python
标准库与 `requests`。

### 发布自动化与 rollback

bundle 发布不能依赖手工复制 wheel URL。实施计划需要包含 CI 或脚本化步骤：

```text
build wheel
  -> calculate sha256
  -> upload to OSS
  -> emit build args:
       MATMASTER_BOHRIUM_TRANSFER_URL
       MATMASTER_BOHRIUM_TRANSFER_SHA256
  -> Dockerfile.remote consumes build args
```

协议应尽量稳定，`protocol_version` 在 v1 发布后的一个稳定窗口内避免频繁
major bump。需要破坏性变更时，先让 Worker 支持新旧两个 protocol major，
再滚动远端镜像。

rollback 策略：

- 在 Phase A 到 Phase C 保留 legacy transfer feature flag：
  `BOHRIUM_TRANSFER_USE_LEGACY=1`。
- legacy path 仍可走现有 `bohrium-sdk` / helper 逻辑，用于新包或远端镜像
  出问题时快速回退。
- Phase D 才移除 `bohrium-sdk` 依赖。移除后该 flag 失效，回滚需要回退镜像
  或代码版本。
- 每个 phase 必须能独立发布和回退到上一 phase。

## 分阶段交付

这份设计描述最终状态，但实施不能一次性合并。建议拆成至少四个 phase。

### Phase A: 包提取与安装链路

目标：建立独立 `packages/bohrium-transfer`、wheel 构建、OSS 安装和远端
entrypoint，但功能尽量等价于当前实现。

范围：

- 将现有 `upload.py`、`artifacts.py`、`remote_transfer_helper.py` 的数据面
  逻辑搬到独立 package，允许暂时继续依赖 `bohrium-sdk`。
- 构建 wheel/sdist 与 sha256。
- `Dockerfile.remote` 从 OSS 或本地 wheel 安装该 package。
- Worker 可通过远端 entrypoint 执行 `version --json`。

验收：

- 不改变 submit/download 行为。
- 远端 package 安装链路可用。
- legacy feature flag 可回退到现有路径。

### Phase B: 移除运行时源码复制

目标：远端只调用预装 package，不再使用 `_helper_source()` 复制源码。

范围：

- `remote_runner.py` 改为调用
  `python -m matmaster_bohrium_transfer.remote ...`。
- 实现版本探测、capability 检查、exit code 三态处理。
- 缺失或不兼容时给出可操作错误。

验收：

- 远端 submit/download 不再写 helper 源码。
- 旧远端镜像失败信息清晰。
- legacy feature flag 可回退。

### Phase C: 重写上传数据面

目标：实现 SDK-free multipart upload、并发、manifest、part retry 和
`ZIP_STORED input.zip`。

范围：

- 自研 storeHost multipart client。
- 上传 manifest 与 token 安全。
- retry/backoff。
- archive fingerprint。
- 本地与远端 submit 走新 upload path。

验收：

- 本地与远端 submit 不再调用 `Tiefblue` 上传。
- 上传不会整体读入大文件。
- 同会话内 token 有效期内可 resume 已完成 parts。
- 上传失败不调用 `job/add`。
- legacy feature flag 可回退。

### Phase D: 重写下载数据面并移除 bohrium-sdk

目标：实现 SDK-free download、Range resume、并发 Range、sandbox fallback
链路，并从依赖中移除 `bohrium-sdk`。

范围：

- Range probe 与并发 Range download。
- `.part` manifest resume。
- 完整保留 sandbox zip/log/iterate fallback 链。
- 从 `pyproject.toml` 与 `Dockerfile.remote` 移除 `bohrium-sdk`。

验收：

- sandbox 与非 sandbox download 保持现有兼容性。
- Range 支持时可并发续传。
- Range 不支持或缺少 `Content-Length` 时明确退化。
- `bohrium-sdk` 不再是运行依赖。

## 主项目改动范围

预期改动文件或目录：

```text
packages/bohrium-transfer/
scripts/build_bohrium_transfer_bundle.py
Dockerfile.remote
pyproject.toml
uv.lock
matmaster/bohrium/upload.py
matmaster/bohrium/artifacts.py
matmaster/bohrium/remote_transfer_helper.py
matmaster/tools/builtin/bohrium_tool/transfers.py
matmaster/tools/builtin/bohrium_tool/remote_runner.py
matmaster/tools/builtin/bohrium_tool/tool.py
tests/...
AGENTS.md
```

`remote_transfer_helper.py` 可在迁移完成后删除，或保留为短期兼容层，但
不应再作为远端运行时源码复制机制。

AGENTS.md 需要更新新的约定：

- builtin Bohrium submit/download 不再依赖 `bohrium-sdk`。
- 大文件传输走 `matmaster_bohrium_transfer`。
- 远端节点必须预装专有 transfer package。
- 远端版本不匹配时失败，不做源码复制 fallback。

## 测试策略

### 独立 transfer package 单元测试

`client.py`：

- multipart init/upload/complete 的 URL、header、payload 与当前 Tiefblue
  行为一致。
- `X-Storage-Param` base64 JSON 正确。
- `Authorization: Bearer <token>` 正确。
- HTTP 4xx/5xx 分类为 typed errors。
- 真正的 storeHost contract test 跑通 small file 的 init、part upload、
  complete、download、iterate。该测试可以是手动或受环境变量保护的可选测试，
  但 Phase C 合并前必须至少在真实环境执行并记录结果。

`multipart.py`：

- 大文件不会整体读入内存。
- part 按 offset/size 读取。
- 并发上传不超过配置值。
- part 失败会重试。
- manifest 中已完成 part 会跳过。
- complete 使用正确 partString 顺序。

`manifest.py`：

- manifest 文件权限为 `0600`。
- token 可持久化但日志脱敏。
- 指纹不匹配时拒绝恢复。
- lock 防止并发写。

`archive.py`：

- 默认 `ZIP_STORED`。
- 非 ASCII 文件名 round trip 正常。
- 路径穿越被阻止。

`download.py`：

- Range 支持时从 `.part` 续传。
- Range 支持时并发下载不超过 `download_concurrency`。
- Range 不支持时明确退化。
- 缺少 `Content-Length` 时 `resume_supported=false` 且 `bytes_total=null`。
- 解压到 staging 后原子发布。
- zip slip 被阻止。
- sandbox fallback 链完整保留：
  `{job_id}.zip` -> 非 `task.zip` zip -> 任意 zip -> `resultUrl` zip ->
  iterate 单对象 -> log-only。

### Remote CLI 测试

- `version --json` 输出协议、版本、capabilities。
- `upload-submit --payload-file` 不把 token 打到 stdout/stderr。
- payload 文件读取后删除或清空。
- CLI 失败只输出 safe JSON。
- 缺失字段、schema mismatch、版本不兼容有清晰错误。

### 主项目集成测试

- builtin `Bohrium submit` 本地路径调用独立包，不再 import `bohrium-sdk`。
- 远端路径调用版本探测和 remote CLI，不再复制 helper 源码。
- `paths.py` / `models.py` 仍留在主项目，transfer package 不接管 tool path
  resolution。
- 上传失败不调用 `job/add`。
- 成功后 `job/add` payload 保持当前兼容语义。
- download 本地和远端路径返回现有 public fields，并增加 transfer summary。
- `pyproject.toml` 与 `Dockerfile.remote` 移除 `bohrium-sdk` 依赖。

### 可选真实环境验证

- sandbox 提交小型 `ZIP_STORED input.zip`。
- 非 sandbox 提交小型 `ZIP_STORED input.zip`。
- 上传 100MB、5GB、可选更大文件，观察吞吐与内存。
- 人工中断上传后重试，确认 manifest resume。
- 人工中断下载后重试，确认 Range resume 或明确退化。

## 成功标准

- `bohrium-sdk` 不再是主项目或远端镜像的运行依赖。
- builtin Bohrium 本地 submit 能上传 `ZIP_STORED input.zip` 并成功 `job/add`。
- builtin Bohrium 远端 submit 不经 Worker 中转大文件。
- 上传和下载内存占用有明确上界，不能整体读入大文件。若实现使用流式 request
  body，峰值应接近 `concurrency * io_buffer_size + overhead`；若实现暂时
  缓冲完整 part，峰值上界必须不超过 `concurrency * part_size + overhead`，
  并在测试中显式验证。
- multipart 上传支持并发和 part 级 retry。
- 同会话、token 有效期内重试能基于 manifest 跳过已完成上传 part。
- download 支持 Range 时可以从 `.part` 续传并支持并发 range parts。
- Range 不支持时明确记录 `resume_supported=false`。
- sandbox download fallback 链不回归。
- token 不出现在普通日志、remote stdout、tool result。
- 远端 package 缺失或版本不兼容时给出可操作错误。
- 后续 SSE/Redis 进度接入可以通过新增 `ProgressSink` 完成。
