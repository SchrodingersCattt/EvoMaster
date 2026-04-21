# Bohrium Direct Remote Transfer Design

## Context

The builtin `Bohrium` tool currently supports `submit`, `poll`, `download`,
`kill`, `list_images`, and `list_machines`.

For `submit`, the current transfer behavior is:

- Local `input_dir`: the Worker zips the directory and uploads `input.zip` to
  the Bohrium `storeHost` returned by `job/create`.
- Remote `/share/...` or `/personal/...` `input_dir`: the remote Bohrium node
  zips the directory, the Worker downloads that zip through SSH/SFTP, and then
  the Worker uploads the zip to Bohrium `storeHost`.

For `download`, the current transfer behavior is:

- Local `result_dir`: the Worker downloads result artifacts and extracts them
  locally.
- Remote `/share/...` or `/personal/...` `result_dir`: the Worker downloads
  result artifacts into a local staging directory, then uploads that directory
  back to the remote node through SSH/SFTP.

The remote-path submit and download flows therefore perform a large-file data
round trip through the Worker. This design removes that Worker data-plane hop
for the builtin `Bohrium` tool.

## Goals

- Keep the scope limited to builtin `Bohrium(action="submit")` and
  `Bohrium(action="download")`.
- Preserve local submit/download behavior.
- For remote `/share/...` and `/personal/...` submit inputs, upload directly
  from the remote Bohrium node to Bohrium `storeHost`.
- For remote `/share/...` and `/personal/...` download targets, download and
  extract directly from Bohrium result storage to the remote Bohrium node.
- Keep Worker responsibilities on the control plane: `job/create`, `job/add`,
  `get_job_detail`, remote helper orchestration, and small JSON metadata.
- Do not silently fall back to the old Worker data-transfer path when a remote
  direct transfer fails.
- Keep the public tool response fields stable where possible.

## Non-Goals

- Do not change MCP calculation path materialization or its OSS upload helpers.
- Do not move chat response figures to Bohrium `storeHost`; response figures
  remain product-display assets, not computation input/output transfer assets.
- Do not change workspace archival or general product OSS uploads.
- Do not introduce real Bohrium integration tests as CI requirements.
- Do not change relative path semantics. Remote direct transfer applies only to
  explicit `/share/...` and `/personal/...` paths.

## Chosen Approach

Use a lightweight transfer-executor layer plus a remote helper module.

```text
BohriumTool
  -> resolve_input_source / resolve_download_target
  -> Transfer Orchestrator
      -> LocalSubmitUploader
      -> RemoteSubmitUploader
      -> LocalResultDownloader
      -> RemoteResultDownloader
```

The local implementations wrap the current Worker-side behavior. The remote
implementations call a Python helper on the active Bohrium SSH session.

Suggested module boundaries:

```text
matmaster/bohrium/remote_transfer_helper.py
  Standalone Python helper copied to and executed on the remote node.

matmaster/tools/builtin/bohrium_tool/remote_runner.py
  Worker-side helper deployment, payload writing, command execution, JSON
  parsing, and cleanup.

matmaster/tools/builtin/bohrium_tool/transfers.py
  Transfer executors and shared path/result data structures.
```

The helper is copied to a remote temporary directory for each operation instead
of relying on skill sync or a persistent `/share/.matmaster` copy. This ensures
the remote helper version matches the Worker code version for that run.

## Submit Flow

The new submit flow should be:

```text
BohriumTool._submit
  -> build_bohrium_context(require_project=True)
  -> resolve_input_source(input_dir)
  -> create_job(ctx, job_name)
  -> select uploader
       local_dir        -> LocalSubmitUploader
       remote_share_dir -> RemoteSubmitUploader
  -> uploader.upload(...)
  -> add_job(ctx, create_data, upload, image, cmd, machine, job_name, disk_size)
```

This changes the current ordering. Today the code prepares the input archive
before `job/create`. Remote direct upload requires `storeHost`, `storePath`, and
`token`, so `job/create` must happen before the upload step.

### Local Submit

Local submit remains equivalent to the existing behavior:

```text
local input_dir
  -> Worker creates input.zip
  -> Worker calls Tiefblue(storeHost).upload_From_file_multi_part(...)
  -> uploader returns UploadedArchive(oss_key, download_url)
```

### Remote Submit

Remote submit uses the remote helper:

```text
remote /share input_dir
  -> Worker calls job/create
  -> Worker writes helper.py and payload.json to remote /tmp
  -> remote helper zips input_dir into remote temp input.zip
  -> remote helper uploads input.zip to storeHost/storePath/input.zip
  -> remote helper prints non-sensitive JSON metadata
  -> Worker parses metadata and builds UploadedArchive
  -> Worker calls job/add
```

The helper payload should be written to a remote JSON file, not embedded in the
shell command line.

Example payload:

```json
{
  "input_dir": "/share/case/input",
  "store_host": "https://store.example.com",
  "store_path": "sandbox/jobs/run-1/",
  "token": "upload-token",
  "object_name": "input.zip"
}
```

Example success output:

```json
{
  "ok": true,
  "oss_key": "sandbox/jobs/run-1/input.zip"
}
```

The helper must not print the upload token or a token-bearing `download_url`.
For sandbox mode, the Worker already has `store_host`, `store_path`, and
`token` from `job/create`, so it should construct `UploadedArchive.download_url`
locally after the remote helper reports the uploaded `oss_key`.

The Worker must not call `job/add` if the remote upload fails.

## Download Flow

The new download flow should be:

```text
BohriumTool._download
  -> build_bohrium_context()
  -> resolve_download_target(result_dir)
  -> get_job_detail(ctx, job_id)
  -> reject running status
  -> require success or failure terminal status
  -> select downloader
       local_dir        -> LocalResultDownloader
       remote_share_dir -> RemoteResultDownloader
  -> downloader.download(...)
  -> return success or error payload using existing public fields
```

### Local Download

Local download remains equivalent to the existing behavior:

```text
resultUrl / jobFiles
  -> Worker downloads artifact zip or objects
  -> Worker extracts under local result_dir
  -> Worker returns files and log_tail
```

### Remote Download

Remote download uses the remote helper:

```text
Bohrium result storage
  -> remote helper downloads to remote staging directory
  -> remote helper extracts results
  -> remote helper reads files and log_tail
  -> remote helper atomically publishes staging to result_dir
  -> Worker returns remote result_dir, files, and log_tail
```

The final remote `result_dir` should contain the extracted files directly, not
only a zip archive.

Example payload:

```json
{
  "job_id": "job-123",
  "result_dir": "/share/case/results/job-123",
  "detail_data": {
    "status": 2,
    "resultUrl": "https://store.example.com/api/download/prefix/job-123.zip?token=result-token"
  },
  "ctx": {
    "sandbox": true,
    "base_url": "https://openapi.test.dp.tech",
    "access_key": "ak",
    "project_id": 42
  }
}
```

Example success output:

```json
{
  "ok": true,
  "result_dir": "/share/case/results/job-123",
  "files": ["log", "OUT.ABACUS/running_scf.log"],
  "log_tail": "..."
}
```

## Remote Helper Deployment

For every remote submit or download operation, the Worker should:

1. Create a remote temp directory such as
   `/tmp/matmaster_bohrium_transfer_<uuid>`.
2. Write `helper.py` to that directory.
3. Write `payload.json` to that directory.
4. Execute:

```text
python3 /tmp/matmaster_bohrium_transfer_<uuid>/helper.py <subcommand> --payload-file /tmp/matmaster_bohrium_transfer_<uuid>/payload.json
```

5. Parse the helper's JSON output.
6. Remove the remote temp directory in a cleanup step.

This avoids putting tokens in the command line and avoids relying on persistent
helper files under `/share`.

## Remote Result Publishing

Remote download should use staging and replacement to avoid publishing partial
results.

Suggested directory strategy:

```text
staging = result_dir + ".tmp.<uuid>"
backup  = result_dir + ".bak.<uuid>"
```

Algorithm:

1. Download and extract into `staging`.
2. If `result_dir` does not exist, move `staging` to `result_dir`.
3. If `result_dir` exists, move `result_dir` to `backup`, then move `staging` to
   `result_dir`, then remove `backup`.
4. If replacement fails after `backup` was created, best-effort restore
   `backup` to `result_dir`.
5. On failure, best-effort remove `staging`.

After a successful remote download, `result_dir` must contain the new extracted
result files.

## Error Handling

Remote direct transfer failures are terminal tool failures for that operation.
There is no fallback to the old Worker data path.

Rules:

- Remote submit upload failure returns a `ToolResult(status="error")`.
- Remote submit upload failure prevents `job/add`.
- Remote download failure returns a `ToolResult(status="error")`.
- Remote download failure must not return a local Worker staging path.
- A helper nonzero exit code is an error.
- A helper `ok=false` JSON result is an error.
- A helper stdout that cannot be parsed as JSON is an error.
- Cleanup failure is logged as a warning and must not hide the original failure.

The helper should emit structured failures with enough context to distinguish:

- missing remote `bohrium-sdk`
- `storeHost` network failure
- upload token failure
- result URL download failure
- zip extraction failure
- result directory publish failure

## Security

Credentials and short-lived transfer tokens must be treated as sensitive.

Required behavior:

- Do not pass access keys or transfer tokens as command-line arguments.
- Do not print access keys or transfer tokens to helper stdout/stderr.
- Do not log raw access keys or tokens in Worker logs.
- Store remote payload files under a temp directory with restrictive
  permissions where practical.
- Delete remote temp payloads in cleanup.
- Redact token-like query parameters in error messages.

The remote node is already part of the user's execution environment, so the
design does not try to hide credentials from the user's own remote session. The
goal is to avoid accidental disclosure through process lists, logs, stdout,
stderr, and leftover temp files.

## Remote Dependencies

Update `Dockerfile.remote` to install `bohrium-sdk>=0.15.0`, matching the Worker
dependency in `pyproject.toml`.

The remote helper should check for:

- `bohrium.resources.tiefblue.Tiefblue`
- `requests`

If either dependency is missing, the helper should fail with a clear diagnostic.

## Testing And Verification

There is no stable real Bohrium environment available for normal CI. Testing
should therefore focus on local unit tests that verify orchestration decisions,
failure semantics, and the helper protocol.

Required local tests:

1. Path routing:
   - local `input_dir` selects local submit uploader
   - `/share/...` and `/personal/...` `input_dir` select remote submit uploader
   - local `result_dir` selects local result downloader
   - `/share/...` and `/personal/...` `result_dir` select remote result
     downloader

2. Remote submit orchestration:
   - remote submit does not call `session.download`
   - remote submit does not use the old remote `prepare_input_archive` download
     path
   - helper success feeds `job/add`
   - helper failure returns submit error and does not call `job/add`

3. Remote download orchestration:
   - remote download does not call local `download_job_artifacts`
   - remote download does not call `session.upload_directory`
   - helper success returns existing public fields
   - helper failure returns error and no local staging path

4. Remote runner protocol:
   - helper and payload are written to a remote temp directory
   - payload is passed through `payload.json`, not command-line JSON
   - JSON output is parsed
   - non-JSON output, `ok=false`, and nonzero exit code become clear errors
   - cleanup is attempted

5. Helper file-system behavior:
   - directory zip preserves relative paths
   - zip extraction works into staging
   - `log_tail` reading and truncation work
   - result directory replacement uses staging and backup
   - token redaction works

Non-required tests:

- No CI test should create a real Bohrium node.
- No CI test should upload to a real `storeHost`.
- No CI test should download real Bohrium job artifacts.
- Real network smoke tests should be manual or optional scripts only.

Suggested manual smoke after deployment:

```text
1. Create /share/direct-transfer-smoke/input on a Bohrium SSH session.
2. Submit that remote input directory with Bohrium(action="submit").
3. Poll until the job reaches a terminal status.
4. Download to /share/direct-transfer-smoke/results.
5. Confirm the remote result directory contains log and output files.
```

## Observability

Worker logs and tool metadata should include non-sensitive transfer diagnostics:

- `submit_transfer_mode=local|remote`
- `download_transfer_mode=local|remote`
- `remote_helper_elapsed_seconds`
- `remote_helper_exit_code`
- `job_id`
- `store_host` without token

These diagnostics replace the old silent fallback behavior. If the remote
environment cannot reach `storeHost`, or lacks `bohrium-sdk`, the user should
see an explicit failure instead of a hidden Worker-mediated transfer.

## Risks

1. Remote node cannot reach `storeHost`.
   - The operation fails directly. No fallback is attempted.

2. Remote `bohrium-sdk` is missing or incompatible.
   - The helper fails with a clear dependency diagnostic.

3. Sandbox result download logic is complex.
   - The helper should preserve current behavior as closely as practical:
     result URL zip, object iteration, log token download, zip fallback, and
     individual object fallback.

4. Partial result directories.
   - Staging plus backup replacement reduces the chance of visible partial
     output.

5. Token leakage.
   - Payload files, redaction, and cleanup reduce accidental leakage through
     logs and process lists.

6. Relative path confusion.
   - This design does not change relative path handling. Remote direct transfer
     requires explicit `/share/...` or `/personal/...` paths.

## Acceptance Criteria

- Remote submit no longer downloads input zips from the remote node to the
  Worker.
- Remote submit uploads `input.zip` from the remote node to Bohrium `storeHost`.
- Remote submit failure does not call `job/add`.
- Remote download no longer downloads artifacts to the Worker before publishing
  to `/share`.
- Remote download writes extracted result files directly under the requested
  remote `result_dir`.
- Remote download failure does not return a Worker-local staging path.
- Local submit/download behavior remains equivalent to current behavior.
- Public tool response fields remain compatible for successful submit/download
  and failed job artifact retrieval.
