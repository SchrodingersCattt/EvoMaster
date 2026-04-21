# Bohrium Direct Remote Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change builtin `Bohrium(action="submit")` and `Bohrium(action="download")` so remote `/share/...` and `/personal/...` paths transfer directly between the Bohrium node and Bohrium storage instead of routing large files through the Worker.

**Architecture:** Keep control-plane calls in `BohriumTool` / Worker. Add a Worker-side `remote_runner.py` that copies a standalone `matmaster/bohrium/remote_transfer_helper.py` to remote `/tmp`, writes a mode-600 payload, executes the helper, parses JSON, and cleans up. `transfers.py` selects local or remote behavior and exposes narrow orchestration functions back to `tool.py`.

**Tech Stack:** Python 3.11+, `zipfile`, `requests`, `bohrium.resources.tiefblue.Tiefblue`, existing session protocol, pytest with monkeypatch fakes.

---

### Task 1: Remote Runner Protocol

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/remote_runner.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py`

- [ ] **Step 1: Write failing runner tests**

Test that `run_remote_helper()`:
- probes the Python binary
- creates `/tmp/matmaster_bohrium_transfer.XXXXXX` with `mktemp -d`
- writes `payload.json` via a file, not command-line JSON
- runs `chmod 700` for the temp dir and `chmod 600` for payload
- rejects non-JSON stdout, `ok=false`, and schema mismatches
- always attempts `rm -rf` cleanup

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py -q
```

Expected before implementation: import or attribute failure for `remote_runner`.

- [ ] **Step 2: Implement runner**

Create:

```python
SCHEMA_VERSION = "v1"

def run_remote_helper(session, *, subcommand: str, payload: dict, timeout: int = 3600) -> dict:
    ...
```

Use `session.exec_bash()` and `session.write_file()`. Do not pass payload JSON in the command line.

- [ ] **Step 3: Verify runner tests**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py -q
```

Expected after implementation: all tests pass.

### Task 2: Standalone Remote Helper

**Files:**
- Create: `matmaster/bohrium/remote_transfer_helper.py`
- Test: `tests/matmaster/bohrium/test_remote_transfer_helper.py`

- [ ] **Step 1: Write failing helper tests**

Cover:
- payload `schema_version` mismatch fails before transfer work
- non-ASCII zip/extract round trip works
- submit upload returns `oss_key` and never prints token-bearing `download_url`
- download publish rejects concurrent same-`result_dir` lock
- result publish uses staging and backup replacement
- token-like query parameters are redacted in helper errors

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_remote_transfer_helper.py -q
```

Expected before implementation: import failure for `remote_transfer_helper`.

- [ ] **Step 2: Implement helper**

Add CLI:

```bash
python remote_transfer_helper.py upload-submit --payload-file /tmp/.../payload.json
python remote_transfer_helper.py download-results --payload-file /tmp/.../payload.json
```

Every JSON output includes `schema_version`, `ok`, and non-sensitive metadata. The helper unlinks payload after reading, validates schema before filesystem/network transfer, and classifies structured errors.

- [ ] **Step 3: Verify helper tests**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_remote_transfer_helper.py -q
```

Expected after implementation: all tests pass.

### Task 3: Remote Submit Integration

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`

- [ ] **Step 1: Write failing submit integration tests**

Cover:
- remote submit does not call `session.download`
- remote helper success feeds `job/add`
- remote helper failure prevents `job/add`
- remote upload failure after `job/create` reports `created_job_ref`
- local submit remains Worker-side

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py -q
```

Expected before implementation: remote submit still calls `session.download`.

- [ ] **Step 2: Implement submit selection**

For local input, preserve current local zip + Worker upload behavior. For `remote_share_dir`, call `create_job`, execute remote helper upload, build `UploadedArchive` with Worker-local `_build_download_url()`, then call `job/add`.

- [ ] **Step 3: Verify submit integration**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py -q
```

Expected after implementation: focused submit tests pass.

### Task 4: Remote Download Integration

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`

- [ ] **Step 1: Write failing download integration tests**

Cover:
- remote download does not call local `download_job_artifacts`
- remote download does not call `session.upload_directory`
- sandbox log token is fetched locally and payload excludes long-lived `access_key`
- helper failure returns tool error and no Worker-local staging path
- local download remains Worker-side

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_download.py -q
```

Expected before implementation: remote download still calls local artifact download and `upload_directory`.

- [ ] **Step 2: Implement download selection**

For local targets, keep current `download_job_artifacts()` path. For remote targets, call `get_job_detail()` locally, prepare helper payload from `detail_data`, prefetch sandbox log token locally when possible, execute helper download, and return existing public response fields.

- [ ] **Step 3: Verify download integration**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_download.py -q
```

Expected after implementation: focused download tests pass.

### Task 5: Remote Image Dependency And Focused Verification

**Files:**
- Modify: `Dockerfile.remote`

- [ ] **Step 1: Update dependency**

Add `bohrium-sdk>=0.15.0` to the remote image Python dependencies. Keep existing `bohrium-open-sdk==1.0.5` unless removal is verified safe.

- [ ] **Step 2: Run focused verification**

Run:

```bash
uv run pytest \
  tests/matmaster/bohrium/test_remote_transfer_helper.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster/bohrium/test_upload.py \
  tests/matmaster/bohrium/test_artifacts.py \
  -q
```

Expected: all focused local tests pass. No real Bohrium node, `storeHost`, or job artifact network is required.
