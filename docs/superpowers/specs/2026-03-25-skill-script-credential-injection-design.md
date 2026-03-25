# Skill Script Credential Injection

## Problem

bohrium-job skill scripts (`submit_job.py`, `poll_job.py`, `list_images.py`, `list_machines.py`) read `BOHRIUM_ACCESS_KEY` from `os.environ`. The new `SkillTool` (`matmaster/tools/skill_tool.py`) builds and executes shell commands but never injects credentials into the script process environment.

In the main branch, `evomaster/agent/tools/skill.py` calls `build_bohrium_skill_remote_env(session)` which reads `session._bohrium_credentials` (set by the service layer via `apply_run_credentials_to_session`) and writes env vars to a remote temp file via SFTP before sourcing it. The refactored `SkillTool` has no equivalent mechanism.

## Design

A new module `matmaster/tools/script_env.py` bridges session credentials to script process environment. It has two orthogonal concerns:

1. **Collect** (what): declarative mapping from session credential attributes to env var names
2. **Inject** (how): transport-adaptive strategy based on session capabilities

### Public API

One function:

```python
def inject(cmd: str, session: Any) -> str:
```

Returns the command wrapped with env var injection. No credentials found returns `cmd` unchanged.

### Credential Mapping (declarative)

```python
_CREDENTIAL_SOURCES: list[tuple[str, dict[str, str]]] = [
    ("_bohrium_credentials", {
        "access_key":  "BOHRIUM_ACCESS_KEY",
        "project_id":  "BOHRIUM_PROJECT_ID",
        "user_id":     "BOHRIUM_USER_ID",
        "user_no":     "BOHRIUM_USER_NO",
    }),
]
```

Adding a new credential type means adding one tuple to this list.

`BOHRIUM_BASE_URL` is supplemented from `src.utils.constant.BOHRIUM_OPENAPI_HOST` (with `ImportError` fallback to `https://open.bohrium.com`) whenever `BOHRIUM_ACCESS_KEY` is present.

### Injection Strategies

**Strategy selection**: always attempt file-based injection first via `session.write_file()`. All session types (SSH, Docker, Local) implement `write_file` in `BaseSession`, so the file strategy is the default path. Inline prefix is a failure-only fallback when `write_file` raises.

**File-based** (default for all session types):

```bash
# 1. write_file() writes /tmp/.mm_env_<uuid12> via SFTP/copy (off tmux channel)
# 2. exec_bash() tightens permissions immediately after
chmod 600 /tmp/.mm_env_<uuid12>

# 3. Command wrapped as:
( . /tmp/.mm_env_<id> && <original_cmd>; _ec=$?; rm -f /tmp/.mm_env_<id>; exit $_ec )
```

- Credentials written via SFTP/file copy, never appear in tmux command history
- `chmod 600` immediately after write -- race window mitigated by UUID unpredictability (uuid4 = 122 bits entropy)
- `_ec` preserves original exit code through `rm` cleanup
- Subshell `( )` isolates env vars from tmux session state

**Inline prefix** (fallback when `write_file` or `chmod` raises):

```bash
BOHRIUM_ACCESS_KEY='xxx' BOHRIUM_PROJECT_ID='123' <original_cmd>
```

- Credentials visible in command string (acceptable: only reached on transport failure)
- Logged as warning when triggered

### `_collect` Validation Rules

Based on `build_bohrium_skill_remote_env()` in `evomaster/env/bohrium.py`, with one deliberate relaxation:

- `_bohrium_credentials` must be a `dict`; skip otherwise
- `access_key` empty or missing returns empty dict (no partial injection)
- `project_id` validated as `int()` -- non-integer values are silently dropped (not injected), matching old helper. This prevents `submit_job.py` from crashing on malformed session data.
- `user_id`, `user_no`: skip if `None`, empty, or `"-1"` after strip
- All injected values stored as `str`

**Deliberate relaxation from old helper**: `project_id` is no longer required for injection to proceed. The old `build_bohrium_skill_remote_env()` returns empty dict when `project_id` is missing or non-integer, blocking all scripts. The new `_collect` injects `access_key` alone when `project_id` is absent, allowing `list_images.py` and `list_machines.py` (which only need `BOHRIUM_ACCESS_KEY`) to work. `submit_job.py` still validates `BOHRIUM_PROJECT_ID` internally and emits its own JSON error if missing.

### SkillTool Integration

In `_run_script()`, one line after `_build_command()`:

```python
from matmaster.tools.script_env import inject as inject_env

cmd = self._build_command(script_path, project_root, script_args, self._session)
cmd = inject_env(cmd, self._session)
result = self._session.exec_bash(cmd, timeout=script_timeout)
```

No changes to `_build_command()`, `__init__()`, or the Tool Protocol interface.

## Files

| File | Action |
|------|--------|
| `matmaster/tools/script_env.py` | Create (~55 lines) |
| `matmaster/tools/skill_tool.py` | Edit (add 2 lines in `_run_script`) |

## Test Plan

| Test | What it covers |
|------|----------------|
| `test_collect_full_credentials` | `_collect` with complete `_bohrium_credentials` returns all env vars |
| `test_collect_ak_only` | `_collect` without `project_id` still returns `BOHRIUM_ACCESS_KEY` (deliberate relaxation) |
| `test_collect_rejects_non_int_project_id` | `project_id="abc"` is silently dropped, AK still injected |
| `test_collect_empty_creds` | Missing or empty `_bohrium_credentials` returns empty dict |
| `test_collect_skips_sentinel_values` | `user_id="-1"`, empty strings are excluded |
| `test_inject_via_file` | Mock `session.write_file` + `session.exec_bash`; verify temp file content, chmod call, subshell wrapping |
| `test_inject_file_permissions` | Verify `exec_bash("chmod 600 ...")` is called after `write_file` |
| `test_inject_fallback_on_write_failure` | `write_file` raises; verify fallback to inline prefix with warning log |
| `test_inject_inline_format` | Verify inline prefix format with proper `shlex.quote` escaping |
| `test_inject_no_creds_passthrough` | No credentials on session; cmd returned unchanged |
| `test_skill_tool_integration` | End-to-end: `SkillTool._run_script` calls `inject_env` before `exec_bash` |

## Properties

| Dimension | Assessment |
|-----------|------------|
| evomaster coupling | Zero -- uses `session.write_file` / `session.exec_bash` (BaseSession protocol) |
| Extensibility | Add credential source = add one tuple to mapping list |
| Security | File strategy: credentials via SFTP (off tmux), chmod 600, UUID-named temp file |
| Degradation | `write_file`/`chmod` failure auto-falls back to inline prefix with warning |
| Testability | `_collect` is pure; injection testable with mock session (2 methods) |
| Invasion | SkillTool adds 1 import + 1 line call; no interface changes |
