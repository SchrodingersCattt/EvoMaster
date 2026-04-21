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

- 去掉 builtin Bohrium submit/download 数据面对 `bohrium-sdk` 的依赖。
- 支持 5GB 到 100GB 级别的材料计算输入和结果传输。
- 上传和下载都必须流式处理，内存占用不能随文件大小线性增长。
- 上传支持可配置并发 multipart，默认保守参数：
  - `part_size = 64MB`
  - `upload_concurrency = 4`
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

### 远端输入目录

```text
BohriumTool._submit
  -> build_bohrium_context(require_project=True)
  -> resolve_input_source(remote /share/... or /personal/...)
  -> create_job(ctx, job_name)
  -> remote version probe
  -> write payload.json to remote temp path with chmod 600
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
  -> stream to out.zip.part
  -> resume from byte offset when Range is supported
  -> atomic rename to complete archive
  -> extract to staging dir
  -> atomic publish to result_dir
  -> return files, log_tail, transfer summary
```

### 远端结果目录

```text
Worker fetches job detail and sandbox log token when needed
  -> remote version probe
  -> write payload.json to remote temp path with chmod 600
  -> ssh/session exec:
       python -m matmaster_bohrium_transfer.remote download-results --payload-file ...
  -> remote package downloads directly from resultUrl/storeHost
  -> Range resume when supported
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
- manifest 记录 URL 指纹、目标路径、已下载字节数、总大小、ETag、
  Last-Modified 等可用信息。
- 重试时从已下载 byte offset 继续。

不支持 Range 时：

- 仍然流式下载，不整体进内存。
- manifest 记录 `resume_supported=false`。
- 失败后只能从头下载。

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
- `part_size`
- `concurrency`
- `file_size`
- `file_mtime_ns`
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

## 安全

- manifest 目录尽量使用 `0700`。
- manifest 文件必须使用 `0600`。
- payload 文件必须使用 `0600`。
- payload 文件读取后尽量删除。
- token、access key、带 token 的 URL 可存在于 manifest 和 payload，但不能进入：
  - 普通日志
  - tool result
  - remote CLI stdout
  - user-visible error
- 所有错误输出经过统一 `redact_secrets()`。
- 远端 CLI stdout 只输出非敏感 JSON summary。
- 若 remote CLI 返回失败，返回 `safe_message`、`stage`、`retryable`、
  `resume_available` 等字段，不返回敏感 payload。

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
  "ok": false,
  "stage": "part_upload",
  "retryable": true,
  "safe_message": "remote upload failed during part upload; retry can resume",
  "transfer_id": "submit-input-...",
  "resume_available": true
}
```

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
- Range 不支持时明确退化。
- 解压到 staging 后原子发布。
- zip slip 被阻止。

### Remote CLI 测试

- `version --json` 输出协议、版本、capabilities。
- `upload-submit --payload-file` 不把 token 打到 stdout/stderr。
- payload 文件读取后删除或清空。
- CLI 失败只输出 safe JSON。
- 缺失字段、schema mismatch、版本不兼容有清晰错误。

### 主项目集成测试

- builtin `Bohrium submit` 本地路径调用独立包，不再 import `bohrium-sdk`。
- 远端路径调用版本探测和 remote CLI，不再复制 helper 源码。
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
- 上传不会整体读入 100MB 以上文件。
- multipart 上传支持并发和 part 级 retry。
- 同会话重试能基于 manifest 跳过已完成上传 part。
- download 支持 Range 时可以从 `.part` 续传。
- Range 不支持时明确记录 `resume_supported=false`。
- token 不出现在普通日志、remote stdout、tool result。
- 远端 package 缺失或版本不兼容时给出可操作错误。
- 后续 SSE/Redis 进度接入可以通过新增 `ProgressSink` 完成。
