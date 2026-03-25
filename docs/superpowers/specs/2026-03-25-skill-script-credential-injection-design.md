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

**Strategy selection**: duck-type check `hasattr(session, "write_file") and callable(session.write_file)`.

**Remote file** (SSH sessions with `write_file`):

```bash
# Writes /tmp/.mm_env_<uuid12>:
export BOHRIUM_ACCESS_KEY='xxx'
export BOHRIUM_PROJECT_ID='123'
export BOHRIUM_BASE_URL='https://open.bohrium.com'

# Wraps command as:
( . /tmp/.mm_env_<id> && <original_cmd>; _ec=$?; rm -f /tmp/.mm_env_<id>; exit $_ec )
```

- Credentials never appear in tmux command history
- `_ec` preserves original exit code through `rm` cleanup
- Subshell `( )` isolates env vars from tmux session state

**Inline prefix** (local sessions, or `write_file` failure fallback):

```bash
BOHRIUM_ACCESS_KEY='xxx' BOHRIUM_PROJECT_ID='123' <original_cmd>
```

- Credentials visible in command string (acceptable for local dev)
- Used as automatic fallback if `write_file` raises

### `_collect` Validation Rules

Matches `build_bohrium_skill_remote_env()` in `evomaster/env/bohrium.py`:

- `_bohrium_credentials` must be a `dict`; skip otherwise
- Each value: skip if `None`, empty string, or `"-1"` after strip
- `access_key` empty returns empty dict (no partial injection)
- All values stored as `str`

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

## Properties

| Dimension | Assessment |
|-----------|------------|
| evomaster coupling | Zero -- duck-type `write_file`, no imports |
| Extensibility | Add credential source = add one tuple to mapping list |
| Security | SSH path: credentials off command line (same as main branch) |
| Degradation | `write_file` failure auto-falls back to inline prefix |
| Testability | `_collect` and `_inline` are pure functions; no mock needed |
| Invasion | SkillTool adds 1 import + 1 line call; no interface changes |
