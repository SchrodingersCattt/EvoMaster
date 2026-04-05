# Bohrium Tool Remote Input Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add remote shared-directory support to the built-in `Bohrium` tool so `submit` can accept local paths, relative paths, and `/share/...` or `/personal/...` directories without changing the existing Bohrium OpenAPI submission contract.

**Architecture:** Keep `job/create -> Tiefblue upload -> job/add` unchanged, and insert a file-level input-preparation layer inside `matmaster/tools/builtin/bohrium_tool.py`. That layer classifies `input_dir`, validates local or remote access, materializes a local temporary `input.zip`, and returns cleanup metadata to `_submit()`.

**Tech Stack:** Python 3.10+, pytest, requests, stdlib `tempfile` / `zipfile` / `tarfile`, existing session protocol methods `exec_bash()` and `download()`

**Execution Skills:** `@superpowers/executing-plans` in a fresh execution session; `@superpowers/subagent-driven-development` only if executing inside the current session.

**Spec:** `docs/plans/2026-04-05-bohrium-tool-remote-input-design.md`

**Read First:** `matmaster/tools/builtin/bohrium_tool.py`, `matmaster/tools/builtin/base.py`, `matmaster/integration/runtime_bridge/path_policy.py`, `matmaster/types/session.py`, `matmaster/sessions/ssh.py`, `matmaster/sessions/local.py`, `tests/matmaster/tools/builtin/test_bohrium_tool.py`, `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`

---

## Guardrails

- Do not change Bohrium OpenAPI paths, payload field names, or Tiefblue upload semantics.
- Do not introduce project OSS URL upload into the Bohrium submission path.
- Do not touch `playground-skills/bohrium-job` or any script under that directory.
- Keep the change scoped to the built-in `Bohrium` tool and its tests.
- Keep file-preparation helpers as file-level shared functions inside `matmaster/tools/builtin/bohrium_tool.py`, not as `BohriumTool` instance methods except for final call wiring.
- Preserve current local directory behavior, including automatic `> log 2>&1` suffixing.
- Use `uv run` for all verification commands.

---

## File Map

| File | Action | Responsibility |
| --- | --- | --- |
| `matmaster/tools/builtin/bohrium_tool.py` | Modify | Add file-level input bundle preparation helpers and wire `_submit()` through them |
| `tests/matmaster/tools/builtin/test_bohrium_tool.py` | Modify | Add local-relative and remote shared-directory regression tests |
| `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py` | Modify | Add one end-to-end regression proving built-in Bohrium tool handles `/share/...` with session and rejects it without session |
| `docs/plans/2026-04-05-bohrium-tool-remote-input-design.md` | Reference | Approved design contract |

---

## Task 1: Add failing tests for remote `input_dir` classification

**Files:**

- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Modify: `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`

**Step 1: Write the failing unit tests**

Add focused tests before implementation:

```python
def test_submit_remote_share_without_session_errors(self, tmp_path, monkeypatch):
    _patch_bridge(monkeypatch)
    tool = BohriumTool(workdir=tmp_path)

    result = asyncio.run(
        tool.execute(
            {
                "action": "submit",
                "input_dir": "/share/Pd111_submit",
                "image": "registry.dp.tech/dptech/abacus:LTSv3.10.1",
                "cmd": "mpirun -np 16 abacus",
            }
        )
    )

    assert result.status == "error"
    assert "remote session" in result.content.lower()
```

```python
def test_submit_relative_input_dir_resolves_under_workdir(self, tmp_path, monkeypatch):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "INPUT").write_text("data", encoding="utf-8")
    ...
    result = asyncio.run(
        tool.execute(
            {
                "action": "submit",
                "input_dir": "inputs",
                "image": "test:latest",
                "cmd": "echo hi",
            }
        )
    )
    assert result.status == "success"
```

```python
def test_submit_remote_share_with_session_downloads_bundle(self, tmp_path, monkeypatch):
    session = FakeRemoteSession(...)
    tool = BohriumTool(session=session, workdir=tmp_path)
    ...
    result = asyncio.run(
        tool.execute(
            {
                "action": "submit",
                "input_dir": "/share/Pd111_submit",
                "image": "test:latest",
                "cmd": "echo hi",
            }
        )
    )
    assert result.status == "success"
    assert session.exec_calls
    assert session.download_calls
```

For the integration file, add one regression that asserts the built-in tool uses the session-backed remote path instead of reporting local `input_dir not found`.

**Step 2: Run the target tests to confirm they fail**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -k "submit and (remote_share or relative_input_dir)" -v
```

Expected: FAIL because `BohriumTool._submit()` still calls `Path(input_dir).is_dir()` directly and has no remote directory path handling.

**Step 3: Commit the failing-test checkpoint**

```bash
git add tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py
git commit -m "test: cover Bohrium remote input directories"
```

---

## Task 2: Add file-level bundle preparation helpers in `bohrium_tool.py`

**Files:**

- Modify: `matmaster/tools/builtin/bohrium_tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`

**Step 1: Add the failing helper-oriented tests**

Extend unit tests to pin helper behavior:

```python
def test_remote_input_dir_missing_directory_surfaces_remote_error(...):
    ...
    assert "remote input_dir not found" in result.content.lower()


def test_remote_input_dir_packaging_failure_surfaces_stderr(...):
    ...
    assert "failed to package remote input_dir" in result.content.lower()
```

**Step 2: Run helper-focused tests to confirm they fail**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -k "remote_input_dir_missing or packaging_failure" -v
```

Expected: FAIL because the helper layer does not exist yet.

**Step 3: Implement the minimal helper layer**

In `matmaster/tools/builtin/bohrium_tool.py`, add file-level helpers before `class BohriumTool`:

```python
@dataclass
class PreparedBohriumBundle:
    zip_path: Path
    source_kind: str
    normalized_input_dir: str
    temp_root: Path


def prepare_bohrium_input_bundle(*, input_dir: str, workdir: Path | None, session: Any | None) -> PreparedBohriumBundle:
    ...
```

Add narrow helpers for:

- classifying `input_dir`
- resolving relative paths under `workdir`
- creating local `input.zip`
- creating and downloading a remote temporary archive
- cleaning temporary files

Keep the helpers independent from `ToolResult`.

**Step 4: Run the helper-focused tests to verify they pass**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -k "remote_share or relative_input_dir or packaging_failure" -v
```

Expected: PASS for the new helper-oriented tests.

**Step 5: Commit the helper layer**

```bash
git add matmaster/tools/builtin/bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool.py
git commit -m "feat: prepare Bohrium remote input bundles"
```

---

## Task 3: Wire `_submit()` through the new helper without changing Bohrium API semantics

**Files:**

- Modify: `matmaster/tools/builtin/bohrium_tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Test: `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`

**Step 1: Write the failing submit-path regression**

Add or refine a test that asserts:

- `_submit()` no longer checks `Path("/share/...").is_dir()`
- upload still sends `input.zip`
- sandbox and standard-HPC payload shapes stay unchanged

```python
assert upload_calls[0][0].endswith("input.zip")
assert post_calls[1][1]["ossPath"]
assert post_calls[1][1]["cmd"].endswith("> log 2>&1")
```

**Step 2: Run the submit-path regression to confirm failure**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -k "submit and (remote_share or defaults_to_sandbox)" -v
```

Expected: FAIL until `_submit()` is switched to use `prepare_bohrium_input_bundle(...)`.

**Step 3: Implement the `_submit()` integration**

Modify `_submit()` so it:

1. validates required fields
2. calls `prepare_bohrium_input_bundle(...)`
3. uses `bundle.zip_path` instead of direct `input_path.rglob('*')`
4. wraps bundle cleanup in `try/finally`
5. preserves all existing create/upload/add payload semantics

Keep this part explicitly unchanged:

```python
create_resp = _post(...)
upload_resp = tf_client.upload_from_file_multi_part(...)
add_resp = _post(...)
```

Only the source of the local zip file should change.

**Step 4: Run the submit regression tests**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -k "bohrium_tool_submit" -v
```

Expected: PASS.

**Step 5: Commit the submit wiring**

```bash
git add matmaster/tools/builtin/bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py
git commit -m "fix: support remote input directories in Bohrium tool"
```

---

## Task 4: Add cleanup and edge-case coverage

**Files:**

- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Modify: `matmaster/tools/builtin/bohrium_tool.py`

**Step 1: Write the failing edge-case tests**

Cover:

- empty directory handling
- file passed instead of directory
- remote cleanup attempted after download or packaging failure
- `/share` and `/personal` are remote-share paths, but `/tmp/...` is not

Example:

```python
def test_submit_file_path_instead_of_directory_errors(...):
    file_path = tmp_path / "INPUT"
    file_path.write_text("data", encoding="utf-8")
    ...
    assert "not a directory" in result.content.lower()
```

```python
def test_remote_temp_archive_cleanup_is_attempted(...):
    ...
    assert any("rm -f" in cmd for cmd in session.exec_calls)
```

**Step 2: Run the edge-case tests to confirm they fail**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -k "empty_directory or not_a_directory or cleanup" -v
```

Expected: FAIL until cleanup and edge handling are fully implemented.

**Step 3: Implement the minimal edge handling**

Update helper functions so they:

- reject non-directory inputs cleanly
- decide and document empty-directory behavior
- always attempt remote cleanup in `finally`
- always clean local temp trees in `finally`

Prefer explicit `ValueError` / `RuntimeError` messages that preserve root cause.

**Step 4: Run the edge-case tests again**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -k "empty_directory or not_a_directory or cleanup" -v
```

Expected: PASS.

**Step 5: Commit edge-case coverage**

```bash
git add matmaster/tools/builtin/bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool.py
git commit -m "test: harden Bohrium input bundle edge cases"
```

---

## Task 5: Run the full Bohrium tool verification sweep

**Files:**

- Modify: none expected
- Verify: `matmaster/tools/builtin/bohrium_tool.py`
- Verify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Verify: `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`

**Step 1: Run the focused Bohrium test suite**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -v
```

Expected: PASS.

**Step 2: Run a broader safety check touching Bohrium registration and runtime bridge behavior**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/tools/test_script_env.py -k "bohrium" -v
```

Expected: PASS, proving the remote-input change did not break built-in tool registration or credential usage.

**Step 3: If any verification fails, fix the minimal issue and rerun**

Do not expand scope beyond the built-in `Bohrium` tool unless the failure proves a direct regression caused by this work.

**Step 4: Commit the final verified state**

```bash
git add matmaster/tools/builtin/bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py
git commit -m "fix: support remote shared input directories in Bohrium tool"
```

---

## Implementation Notes

- Prefer `session.exec_bash()` for remote directory checks and archive creation because the session protocol has `download()` and `exec_bash()` but no `is_dir()`.
- Prefer remote `tar.gz` as the transport format, then normalize to local `input.zip` before Bohrium upload.
- Keep error messages distinct for:
  - missing remote session
  - remote directory missing
  - remote packaging failure
  - remote download failure
- Keep the helper layer independent from `ToolResult` so testing stays simple.

## Verification Checklist

- `input_dir="inputs"` resolves under `workdir`
- `input_dir="/abs/local/dir"` still works
- `input_dir="/share/..."` works with active session
- `input_dir="/share/..."` fails clearly without session
- upload still sends `input.zip`
- sandbox payloads and standard HPC payloads remain unchanged

Plan complete and saved to `docs/plans/2026-04-05-bohrium-tool-remote-input.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
