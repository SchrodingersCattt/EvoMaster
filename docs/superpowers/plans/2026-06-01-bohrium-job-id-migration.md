# Bohrium Job ID Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛 Bohrium builtin tool 的公开作业 ID 协议，只向模型暴露 canonical `job_id`，并移除顶层 `bohr_job_id`。

**Architecture:** `job/add` 返回的 `jobId` 是 MatMaster canonical `job_id`，用于 `poll`、`download`、`kill`、registry 和事件重建。`bohrJobId` 不进入模型可见 content；若真实 sandbox file-token 契约需要它，只能以内部门户 ID 形式存在。本计划按当前代码和测试已假设的轻量路径实现：file-token 继续使用 canonical `job_id`，并通过执行前 gate 验证该假设。

**Tech Stack:** Python 3.11+ via `uv run`, dataclasses, pytest, existing Bohrium builtin tool modules.

---

## Precondition

执行 Task 1 前，先完成 live contract verification：

```text
file-token payload {"filePath": "log", "jobId": "<jobId>"} succeeds
```

如果 live verification 证明 file-token 只能使用 `bohrJobId`，停止执行本计划，并基于
`docs/superpowers/specs/2026-06-01-bohrium-job-id-migration-design.md` 另写内部
`file_token_job_id` 映射计划。不要在本计划上临时加入兼容分支。

## File Structure

Modify `matmaster/tools/builtin/bohrium_tool/models.py`

- 新增 `BohriumSubmittedJob` dataclass。
- 这是 submit 路径的具名返回结构，替代 `(job_id, bohr_job_id)` tuple。

Modify `matmaster/tools/builtin/bohrium_tool/tool.py`

- `submit_job_via_runtime()` 返回 `BohriumSubmittedJob`。
- `_submit()` 的模型可见 JSON content 删除 `bohr_job_id`。
- `_fetch_log_tail()` 调 `get_file_token(..., job_id=...)`。
- `_update_registry()` 继续只从 content 读取 canonical `job_id`。

Modify `matmaster/bohrium/client.py`

- `get_file_token()` 参数从 `bohr_job_id` 重命名为 `job_id`。
- 发送给 Bohrium 的外部 payload key 仍是平台字段 `"jobId"`。

Modify `matmaster/bohrium/artifacts.py`

- sandbox log prefetch 调 `get_file_token(..., job_id=str(job_id))`。

Modify `matmaster/tools/builtin/bohrium_tool/transfers.py`

- remote download sandbox log prefetch 调 `get_file_token(..., job_id=str(job_id))`。

Modify `scripts/test_job_polling.sh`

- 不再等待或使用 `bohr_job_id`。
- 注入文件名改用 canonical `job_id`。

Modify tests:

- `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`
- `tests/matmaster/bohrium/test_client.py`
- `tests/matmaster/bohrium/test_artifacts.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`

No main-code compatibility shim is allowed.

---

### Task 1: Verify Live Contract Gate

**Files:**

- Read: `docs/superpowers/specs/2026-06-01-bohrium-job-id-migration-design.md`
- No code changes.

- [ ] **Step 1: Run the live verification**

Use an existing sandbox Bohrium job whose `job/add` response includes both fields, or submit a small disposable sandbox job manually. Record:

```text
jobId=<job/add jobId>
bohrJobId=<job/add bohrJobId>
GET /openapi/v1/sandbox/job/<jobId> => success
POST /openapi/v1/sandbox/job/file/token with jobId => success
POST /openapi/v1/sandbox/job/file/token with bohrJobId => success or failure
```

- [ ] **Step 2: Decide whether this plan is valid**

Continue only if this line is true:

```text
file-token accepts canonical job_id from job/add jobId
```

Expected: continue with Task 2.

If the expected line is false, stop and report:

```text
Blocked: live Bohrium file-token contract requires bohrJobId; execute the internal file_token_job_id mapping design instead of this light migration plan.
```

---

### Task 2: Lock Public Submit Contract With Failing Tests

**Files:**

- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`

- [ ] **Step 1: Update the existing submit contract test**

In `test_submit_defaults_to_sandbox_and_appends_log`, replace the submit payload assertions with:

```python
        payload = json.loads(result.content)
        assert payload == {
            "success": True,
            "job_id": "job-123",
            "status": "Submitted",
            "use_sandbox": True,
        }
        assert "bohr_job_id" not in payload
```

- [ ] **Step 2: Add a direct return-model test**

Add this test near the submit tests:

```python
    def test_submit_job_via_runtime_returns_named_job_model(
        self, tmp_path, monkeypatch
    ):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "input.inp").write_text("&CONTROL\n", encoding="utf-8")

        post_calls: list[tuple[str, dict]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        def fake_post(base_url, path, access_key, payload, timeout=30):
            del base_url, access_key, timeout
            post_calls.append((path, payload))
            if path == "/openapi/v1/sandbox/job/create":
                return {
                    "code": 0,
                    "data": {
                        "storePath": "sandbox/jobs/run-1/",
                        "storeHost": "https://store.example.com",
                        "token": "token-123",
                        "jobId": "create-job-id",
                    },
                }
            if path == "/openapi/v1/sandbox/job/add":
                return {
                    "code": 0,
                    "data": {
                        "jobId": "job-123",
                        "bohrJobId": "bohr-456",
                    },
                }
            raise AssertionError(f"unexpected path: {path}")

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        _install_fake_sdk_free_upload(monkeypatch, upload_calls)

        submitted = bohrium_tool_module.submit_job_via_runtime(
            input_dir=str(input_dir),
            image="registry.dp.tech/dptech/cp2k:2024.1",
            cmd="cp2k.popt -i input.inp",
            machine="c64_m256_cpu",
            job_name="matmaster-job",
            disk_size=50,
            workdir=tmp_path,
            session=None,
        )

        assert submitted.job_id == "job-123"
        assert submitted.raw_add_response == {
            "jobId": "job-123",
            "bohrJobId": "bohr-456",
        }
        assert not isinstance(submitted, tuple)
```

- [ ] **Step 3: Run the focused test and verify failure**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py::TestBohriumExecution::test_submit_defaults_to_sandbox_and_appends_log tests/matmaster/tools/builtin/test_bohrium_tool.py::TestBohriumExecution::test_submit_job_via_runtime_returns_named_job_model -q
```

Expected:

```text
FAILED ... assert payload == ...
FAILED ... AttributeError: 'tuple' object has no attribute 'job_id'
```

---

### Task 3: Implement Named Submit Result And Remove Public `bohr_job_id`

**Files:**

- Modify: `matmaster/tools/builtin/bohrium_tool/models.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`

- [ ] **Step 1: Add the dataclass**

In `matmaster/tools/builtin/bohrium_tool/models.py`, change imports and add:

```python
from typing import Any
```

```python
@dataclass(frozen=True)
class BohriumSubmittedJob:
    job_id: str
    raw_add_response: dict[str, Any]
```

Do not add `bohr_job_id` or `file_token_job_id` in this light path.

- [ ] **Step 2: Import the dataclass in the tool**

In `matmaster/tools/builtin/bohrium_tool/tool.py`, add to the local model imports:

```python
from .models import BohriumSubmittedJob
```

If another `.models` import already exists after implementation edits, merge imports instead of creating duplicate import lines.

- [ ] **Step 3: Change `submit_job_via_runtime()` return type**

Replace:

```python
) -> tuple[int | str, int | str]:
```

with:

```python
) -> BohriumSubmittedJob:
```

- [ ] **Step 4: Return `BohriumSubmittedJob` from sandbox submit**

Replace the sandbox return block:

```python
        job_id: int | str = str(raw_jid).strip()
        bohr_raw = add_data.get("bohrJobId")
        bohr_job_id = str(bohr_raw).strip() if bohr_raw not in (None, "", 0) else job_id
        return job_id, bohr_job_id
```

with:

```python
        job_id = str(raw_jid).strip()
        return BohriumSubmittedJob(
            job_id=job_id,
            raw_add_response=dict(add_data),
        )
```

- [ ] **Step 5: Return `BohriumSubmittedJob` from non-sandbox submit**

Replace:

```python
    job_id = int(add_data["jobId"])
    bohr_job_id = int(add_data.get("bohrJobId") or add_data["jobId"])
    return job_id, bohr_job_id
```

with:

```python
    job_id = str(add_data["jobId"]).strip()
    return BohriumSubmittedJob(
        job_id=job_id,
        raw_add_response=dict(add_data),
    )
```

This intentionally normalizes non-sandbox `job_id` to `str`, matching registry and event semantics.

- [ ] **Step 6: Remove `bohr_job_id` from `_submit()` content**

Replace:

```python
            job_id, bohr_job_id = submit_job_via_runtime(
```

with:

```python
            submitted = submit_job_via_runtime(
```

Then replace the JSON payload:

```python
                        "job_id": job_id,
                        "bohr_job_id": bohr_job_id,
```

with:

```python
                        "job_id": submitted.job_id,
```

The final content payload must be:

```python
                    {
                        "success": True,
                        "job_id": submitted.job_id,
                        "status": "Submitted",
                        "use_sandbox": ctx.sandbox,
                    },
```

- [ ] **Step 7: Run the focused submit tests**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py::TestBohriumExecution::test_submit_defaults_to_sandbox_and_appends_log tests/matmaster/tools/builtin/test_bohrium_tool.py::TestBohriumExecution::test_submit_job_via_runtime_returns_named_job_model -q
```

Expected:

```text
2 passed
```

- [ ] **Step 8: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/models.py matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool.py
git commit -m "refactor: canonicalize bohrium submit job id"
```

---

### Task 4: Rename File Token Parameter To `job_id`

**Files:**

- Modify: `matmaster/bohrium/client.py`
- Modify: `matmaster/bohrium/artifacts.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Test: `tests/matmaster/bohrium/test_client.py`
- Test: `tests/matmaster/bohrium/test_artifacts.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`

- [ ] **Step 1: Update the client test first**

In `tests/matmaster/bohrium/test_client.py`, replace the call:

```python
    host, path, token = get_file_token(_make_ctx(), file_path="log", bohr_job_id="1")
```

with:

```python
    host, path, token = get_file_token(_make_ctx(), file_path="log", job_id="1")
```

- [ ] **Step 2: Update artifacts fake signature**

In `tests/matmaster/bohrium/test_artifacts.py`, replace:

```python
    def fake_get_file_token(ctx, *, file_path, bohr_job_id):
        return "https://store.example", "prefix/log", "log-token"
```

with:

```python
    def fake_get_file_token(ctx, *, file_path, job_id):
        assert job_id == "job-55"
        return "https://store.example", "prefix/log", "log-token"
```

- [ ] **Step 3: Add an explicit remote download assertion**

In `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`,
`test_download_remote_share_uses_direct_remote_helper` already asserts the external payload:

```python
            assert payload == {'filePath': 'log', 'jobId': 'job-remote'}
```

Keep this assertion. It verifies the renamed internal parameter still produces the same Bohrium API payload.

- [ ] **Step 4: Run renamed-parameter tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_client.py::test_get_file_token tests/matmaster/bohrium/test_artifacts.py::test_download_job_artifacts_delegates_to_transfer_package -q
```

Expected:

```text
FAILED ... TypeError: get_file_token() got an unexpected keyword argument 'job_id'
```

- [ ] **Step 5: Rename the function parameter**

In `matmaster/bohrium/client.py`, replace:

```python
def get_file_token(
    ctx: BohriumContext,
    *,
    file_path: str,
    bohr_job_id: str,
) -> tuple[str, str, str]:
```

with:

```python
def get_file_token(
    ctx: BohriumContext,
    *,
    file_path: str,
    job_id: str,
) -> tuple[str, str, str]:
```

Then replace:

```python
        {"filePath": file_path, "jobId": bohr_job_id},
```

with:

```python
        {"filePath": file_path, "jobId": job_id},
```

- [ ] **Step 6: Update all callers**

In `matmaster/tools/builtin/bohrium_tool/tool.py`, replace:

```python
        host, path, token = get_file_token(ctx, file_path="log", bohr_job_id=job_id)
```

with:

```python
        host, path, token = get_file_token(ctx, file_path="log", job_id=job_id)
```

In `matmaster/bohrium/artifacts.py`, replace:

```python
                bohr_job_id=str(job_id),
```

with:

```python
                job_id=str(job_id),
```

In `matmaster/tools/builtin/bohrium_tool/transfers.py`, replace:

```python
                bohr_job_id=str(job_id),
```

with:

```python
                job_id=str(job_id),
```

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_client.py::test_get_file_token tests/matmaster/bohrium/test_artifacts.py::test_download_job_artifacts_delegates_to_transfer_package tests/matmaster/tools/builtin/test_bohrium_tool_download.py::TestBohriumDownloadExecution::test_download_remote_share_uses_direct_remote_helper -q
```

Expected:

```text
3 passed
```

- [ ] **Step 8: Commit**

```bash
git add matmaster/bohrium/client.py matmaster/bohrium/artifacts.py matmaster/tools/builtin/bohrium_tool/transfers.py matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/bohrium/test_client.py tests/matmaster/bohrium/test_artifacts.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py
git commit -m "refactor: rename bohrium file token job id"
```

---

### Task 5: Add Poll Live Log Coverage

**Files:**

- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`

- [ ] **Step 1: Write the test**

Add this test near the poll tests:

```python
    def test_poll_live_log_uses_canonical_job_id(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[dict] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            assert path == "/openapi/v1/sandbox/job/job-123"
            return {"data": {"status": 1}}

        def fake_post(base_url, path, access_key, payload, timeout=30):
            del base_url, access_key, timeout
            assert path == "/openapi/v1/sandbox/job/file/token"
            calls.append(payload)
            return {
                "code": 0,
                "data": {
                    "host": "https://store.example",
                    "path": "prefix/log",
                    "token": "log-token",
                },
            }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"line1\nline2\n"

        def fake_urlopen(req, timeout=5):
            del req, timeout
            return FakeResponse()

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-123"}))

        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["job_id"] == "job-123"
        assert payload["log_tail"] == "line1\nline2"
        assert calls == [{"filePath": "log", "jobId": "job-123"}]
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py::TestBohriumExecution::test_poll_live_log_uses_canonical_job_id -q
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/tools/builtin/test_bohrium_tool.py
git commit -m "test: cover bohrium poll live log job id"
```

---

### Task 6: Migrate `scripts/test_job_polling.sh`

**Files:**

- Modify: `scripts/test_job_polling.sh`

- [ ] **Step 1: Replace wait target text and variables**

Replace this block:

```bash
echo "== 等待 bohr_job_id（意味着弛豫提交成功并开始监控）..."
BOHR_JOB_ID=""
JOB_ID=""
```

with:

```bash
echo "== 等待 job_id（意味着弛豫提交成功并开始监控）..."
JOB_ID=""
```

- [ ] **Step 2: Remove `bohr_job_id` extraction**

Delete the Python extraction block that assigns `BOHR_JOB_ID`.

Keep only:

```bash
    JOB_ID="$(python - "${SSE_OUT}" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, "r", encoding="utf-8", errors="ignore").read()
matches = re.findall(r'"job_id"\s*:\s*"([^"]+)"', text)
print(matches[-1] if matches else "")
PY
)"
    if [[ -n "${JOB_ID}" ]]; then
      break
    fi
```

- [ ] **Step 3: Update error messages**

Replace:

```bash
echo "ERROR: SSE 请求已提前结束，且未获取到 bohr_job_id。请检查 ${SSE_OUT}"
```

with:

```bash
echo "ERROR: SSE 请求已提前结束，且未获取到 job_id。请检查 ${SSE_OUT}"
```

Replace:

```bash
if [[ -z "${BOHR_JOB_ID}" ]]; then
  echo "ERROR: 在 ${WAIT_TIMEOUT}s 内未获取到 bohr_job_id。请检查 ${SSE_OUT}"
  exit 2
fi

echo "== 已获取 bohr_job_id: ${BOHR_JOB_ID}"
if [[ -n "${JOB_ID}" ]]; then
  echo "== 已获取 job_id: ${JOB_ID}"
fi
```

with:

```bash
if [[ -z "${JOB_ID}" ]]; then
  echo "ERROR: 在 ${WAIT_TIMEOUT}s 内未获取到 job_id。请检查 ${SSE_OUT}"
  exit 2
fi

echo "== 已获取 job_id: ${JOB_ID}"
```

- [ ] **Step 4: Use `job_id` for injection file names**

Replace:

```bash
    python - "${BOHR_JOB_ID}" "${FAKE_LLM_LOG_PATH}" "${INJECT_DIR}" <<'PY'
```

with:

```bash
    python - "${JOB_ID}" "${FAKE_LLM_LOG_PATH}" "${INJECT_DIR}" <<'PY'
```

Inside the Python block, replace:

```python
bohr_job_id = (sys.argv[1] or "").strip()
```

with:

```python
job_id = (sys.argv[1] or "").strip()
```

Replace:

```python
if not bohr_job_id:
    print("ERROR: bohr_job_id is required")
    raise SystemExit(1)
```

with:

```python
if not job_id:
    print("ERROR: job_id is required")
    raise SystemExit(1)
```

Replace:

```python
inject_path = inject_dir / f"{bohr_job_id}.log.inject"
```

with:

```python
inject_path = inject_dir / f"{job_id}.log.inject"
```

- [ ] **Step 5: Grep the script**

Run:

```bash
rg -n "bohr_job_id|BOHR_JOB_ID|bohrJobId" scripts/test_job_polling.sh
```

Expected:

```text
no output
```

- [ ] **Step 6: Commit**

```bash
git add scripts/test_job_polling.sh
git commit -m "test: use canonical bohrium job id in polling script"
```

---

### Task 7: Run Focused Bohrium Test Suite

**Files:**

- No code changes.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -q
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_download.py -q
uv run pytest tests/matmaster/bohrium/test_client.py -q
uv run pytest tests/matmaster/bohrium/test_artifacts.py -q
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py -q
uv run pytest tests/matmaster_bohrium_transfer/test_download.py -q
```

Expected:

```text
all commands pass
```

- [ ] **Step 2: If a focused test fails, inspect before editing**

If any command in Step 1 fails, stop execution of this plan and run the relevant
command again with verbose output. For example, if the first command fails:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -vv
```

Use `superpowers:systematic-debugging` before making any repair. Do not guess at
a fix from the summary line alone.

---

### Task 8: Repo-Wide Cleanup And Acceptance Check

**Files:**

- No code changes unless Step 1 finds disallowed use.

- [ ] **Step 1: Check remaining Python references**

Run:

```bash
rg -n "bohr_job_id|bohrJobId" matmaster src tests scripts packages --glob '!matmaster/skills/**' --glob '!evaluation/**'
```

Expected allowed categories only:

```text
raw add response tests, if explicitly asserting platform raw fields
```

No main-code function parameter, tool schema field, model-visible payload field, or script runtime variable may use `bohr_job_id`.

- [ ] **Step 2: Check public tool schema**

Run:

```bash
uv run python - <<'PY'
from matmaster.tools.builtin.bohrium_tool.tool import BohriumTool

schema = BohriumTool.json_schema
properties = schema["properties"]
assert "job_id" in properties
assert "bohr_job_id" not in properties
assert "file_token_job_id" not in properties
print("Bohrium public schema exposes only job_id")
PY
```

Expected:

```text
Bohrium public schema exposes only job_id
```

- [ ] **Step 3: Check submit content shape**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py::TestBohriumExecution::test_submit_defaults_to_sandbox_and_appends_log -q
```

Expected:

```text
1 passed
```

- [ ] **Step 4: Commit cleanup if needed**

If Step 1 still finds a main-code function parameter, tool schema field,
model-visible payload field, or script runtime variable, stop and report the
exact line. Do not add a broad cleanup commit from this task; the earlier tasks
name every intended migration file explicitly.

Expected report format:

```text
Blocked: unexpected bohr_job_id reference remains at path/to/file.py:123
```

---

### Task 9: Final Verification

**Files:**

- No code changes.

- [ ] **Step 1: Run diff check**

Run:

```bash
git diff --check
```

Expected:

```text
no output
```

- [ ] **Step 2: Review final diff**

Run:

```bash
git log --oneline -8
git status --short -- matmaster/tools/builtin/bohrium_tool/models.py matmaster/tools/builtin/bohrium_tool/tool.py matmaster/bohrium/client.py matmaster/bohrium/artifacts.py matmaster/tools/builtin/bohrium_tool/transfers.py scripts/test_job_polling.sh tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py tests/matmaster/bohrium/test_client.py tests/matmaster/bohrium/test_artifacts.py tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py
```

Expected:

```text
recent commits are limited to Bohrium job ID migration code, tests, and script cleanup
no unstaged migration edits remain
```

- [ ] **Step 3: Report verification summary**

Report:

```text
Implemented Bohrium job ID migration.
Public tool content now exposes job_id only.
get_file_token uses job_id parameter.
Focused Bohrium tests pass.
Remaining bohrJobId references are limited to raw platform response assertions or docs.
```
