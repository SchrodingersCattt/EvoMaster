# Skill Script Credential Injection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject session Bohrium credentials as env vars into skill script processes so bohrium-job scripts can authenticate.

**Architecture:** New `matmaster/tools/script_env.py` module with declarative credential mapping and transport-adaptive injection (file-based default, inline fallback). SkillTool gains one import + one function call.

**Tech Stack:** Python 3.10+, shlex, uuid, pytest, unittest.mock

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `matmaster/tools/script_env.py` | Create | Credential collection + env injection |
| `matmaster/tools/skill_tool.py` | Edit (2 lines) | Call `inject_env` in `_run_script` |
| `tests/matmaster/tools/test_script_env.py` | Create | Unit tests for script_env |
| `tests/test_skill_tool.py` | Edit | Integration test for credential injection |

---

## Task 1: Create `script_env.py` with `_collect` + tests

**Files:**
- Create: `matmaster/tools/script_env.py`
- Create: `tests/matmaster/tools/test_script_env.py`

- [ ] **Step 1: Write `_collect` tests**

```python
# tests/matmaster/tools/test_script_env.py
"""Tests for matmaster.tools.script_env — credential-to-env bridge."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _session_with_creds(**creds) -> MagicMock:
    s = MagicMock()
    s._bohrium_credentials = creds
    return s


def _bare_session() -> MagicMock:
    s = MagicMock(spec=[])  # no attributes
    return s


class TestCollect:
    """Tests for _collect: session credentials -> env dict."""

    def test_full_credentials(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds(
            access_key="ak123", project_id=456, user_id=789, user_no="U001"
        )
        env = _collect(session)
        assert env["BOHRIUM_ACCESS_KEY"] == "ak123"
        assert env["BOHRIUM_PROJECT_ID"] == "456"
        assert env["BOHRIUM_USER_ID"] == "789"
        assert env["BOHRIUM_USER_NO"] == "U001"
        assert "BOHRIUM_BASE_URL" in env

    def test_ak_only_without_project_id(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds(access_key="ak123")
        env = _collect(session)
        assert env["BOHRIUM_ACCESS_KEY"] == "ak123"
        assert "BOHRIUM_PROJECT_ID" not in env

    def test_rejects_non_int_project_id(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds(access_key="ak123", project_id="abc")
        env = _collect(session)
        assert env["BOHRIUM_ACCESS_KEY"] == "ak123"
        assert "BOHRIUM_PROJECT_ID" not in env

    def test_empty_creds_returns_empty(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds()
        env = _collect(session)
        assert env == {}

    def test_no_creds_attr_returns_empty(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _bare_session()
        env = _collect(session)
        assert env == {}

    def test_skips_sentinel_user_id(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds(access_key="ak", user_id="-1")
        env = _collect(session)
        assert "BOHRIUM_USER_ID" not in env

    def test_skips_empty_user_no(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds(access_key="ak", user_no="  ")
        env = _collect(session)
        assert "BOHRIUM_USER_NO" not in env

    def test_project_id_string_int_accepted(self) -> None:
        from matmaster.tools.script_env import _collect

        session = _session_with_creds(access_key="ak", project_id="123")
        env = _collect(session)
        assert env["BOHRIUM_PROJECT_ID"] == "123"
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

Run: `uv run pytest tests/matmaster/tools/test_script_env.py -v`
Expected: ImportError — `matmaster.tools.script_env` does not exist

- [ ] **Step 3: Implement `_collect`**

```python
# matmaster/tools/script_env.py
"""Session credential -> script environment bridge.

Declarative mapping from session credential attributes to POSIX env vars.
Injection strategy adapts to session transport capabilities.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# -- declarative credential mapping ----------------------------------------

_CREDENTIAL_SOURCES: list[tuple[str, dict[str, str]]] = [
    ("_bohrium_credentials", {
        "access_key":  "BOHRIUM_ACCESS_KEY",
        "project_id":  "BOHRIUM_PROJECT_ID",
        "user_id":     "BOHRIUM_USER_ID",
        "user_no":     "BOHRIUM_USER_NO",
    }),
]

# Fields requiring int validation (non-int silently dropped)
_INT_VALIDATED: set[str] = {"project_id"}

# Fields with sentinel skip values
_SKIP_VALUES: set[str] = {"-1"}


def _collect(session: Any) -> dict[str, str]:
    """Build env dict from session credentials."""
    env: dict[str, str] = {}
    for attr_name, mapping in _CREDENTIAL_SOURCES:
        creds = getattr(session, attr_name, None)
        if not isinstance(creds, dict):
            continue
        # access_key is the gate — skip all if absent
        ak = (creds.get("access_key") or "").strip()
        if not ak:
            continue
        for cred_key, env_name in mapping.items():
            val = creds.get(cred_key)
            if val is None:
                continue
            s = str(val).strip()
            if not s or s in _SKIP_VALUES:
                continue
            if cred_key in _INT_VALIDATED:
                try:
                    int(s)
                except (TypeError, ValueError):
                    continue
            env[env_name] = s
    # Supplement BOHRIUM_BASE_URL when AK is present
    if env.get("BOHRIUM_ACCESS_KEY") and "BOHRIUM_BASE_URL" not in env:
        try:
            from src.utils.constant import BOHRIUM_OPENAPI_HOST
            env["BOHRIUM_BASE_URL"] = BOHRIUM_OPENAPI_HOST
        except ImportError:
            env["BOHRIUM_BASE_URL"] = "https://open.bohrium.com"
    return env
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/matmaster/tools/test_script_env.py::TestCollect -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/script_env.py tests/matmaster/tools/test_script_env.py
git commit -m "feat: add script_env._collect for credential-to-env mapping"
```

---

## Task 2: Add injection strategies + tests

**Files:**
- Modify: `matmaster/tools/script_env.py`
- Modify: `tests/matmaster/tools/test_script_env.py`

- [ ] **Step 1: Write injection tests**

Append to `tests/matmaster/tools/test_script_env.py`:

```python
class TestInjectViaFile:
    """Tests for file-based injection strategy."""

    def test_writes_file_and_wraps_command(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak123", project_id=456)
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(return_value={"stdout": "", "stderr": "", "exit_code": 0})

        result = inject("python run.py", session)

        session.write_file.assert_called_once()
        path_arg = session.write_file.call_args[0][0]
        content_arg = session.write_file.call_args[0][1]
        assert path_arg.startswith("/tmp/.mm_env_")
        assert "export BOHRIUM_ACCESS_KEY=" in content_arg

        session.exec_bash.assert_called_once()
        chmod_cmd = session.exec_bash.call_args[0][0]
        assert "chmod 600" in chmod_cmd

        assert result.startswith("( . ")
        assert "python run.py" in result
        assert "rm -f" in result
        assert "_ec=$?" in result

    def test_chmod_called_after_write(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak")
        call_order = []
        session.write_file = MagicMock(side_effect=lambda *a: call_order.append("write"))
        session.exec_bash = MagicMock(
            side_effect=lambda *a, **kw: call_order.append("chmod") or {"stdout": "", "stderr": "", "exit_code": 0}
        )

        inject("cmd", session)
        assert call_order == ["write", "chmod"]


class TestInjectFallback:
    """Tests for inline fallback when write_file fails."""

    def test_fallback_on_write_failure(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak123")
        session.write_file = MagicMock(side_effect=OSError("disk full"))

        result = inject("python run.py", session)
        assert "BOHRIUM_ACCESS_KEY=" in result
        assert "python run.py" in result
        # Should NOT have subshell wrapper
        assert not result.startswith("( . ")

    def test_fallback_on_chmod_failure(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak123")
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(side_effect=OSError("exec failed"))

        result = inject("python run.py", session)
        assert "BOHRIUM_ACCESS_KEY=" in result
        assert not result.startswith("( . ")


class TestInjectInline:
    """Tests for inline prefix format."""

    def test_inline_format(self) -> None:
        from matmaster.tools.script_env import _inline

        env = {"BOHRIUM_ACCESS_KEY": "ak with space", "BOHRIUM_PROJECT_ID": "123"}
        result = _inline("python run.py", env)
        assert "BOHRIUM_ACCESS_KEY='ak with space'" in result
        assert "BOHRIUM_PROJECT_ID='123'" in result
        assert result.endswith("python run.py")


class TestInjectPassthrough:
    """Tests for no-op when no credentials."""

    def test_no_creds_returns_unchanged(self) -> None:
        from matmaster.tools.script_env import inject

        session = _bare_session()
        assert inject("python run.py", session) == "python run.py"
```

- [ ] **Step 2: Run tests — expect FAIL (inject/\_inline not defined)**

Run: `uv run pytest tests/matmaster/tools/test_script_env.py -v -k "not TestCollect"`
Expected: ImportError — `inject` / `_inline` not defined

- [ ] **Step 3: Implement `inject`, `_via_file`, `_inline`**

Append to `matmaster/tools/script_env.py`:

```python
# -- public API ------------------------------------------------------------

def inject(cmd: str, session: Any) -> str:
    """Wrap shell command with session credentials as env vars.

    Always attempts file-based injection first (credentials off command line).
    Falls back to inline prefix if write_file or chmod raises.
    Returns cmd unchanged if no credentials found.
    """
    env = _collect(session)
    if not env:
        return cmd
    try:
        return _via_file(cmd, env, session)
    except Exception as exc:
        logger.warning("Env file injection failed: %s; falling back to inline", exc)
        return _inline(cmd, env)


# -- injection strategies --------------------------------------------------

def _via_file(cmd: str, env: dict[str, str], session: Any) -> str:
    """Write env to remote temp file, source in subshell."""
    path = f"/tmp/.mm_env_{uuid.uuid4().hex[:12]}"
    content = "\n".join(
        f"export {k}={shlex.quote(v)}" for k, v in sorted(env.items())
    ) + "\n"
    session.write_file(path, content)
    session.exec_bash(f"chmod 600 {shlex.quote(path)}")
    return (
        f"( . {shlex.quote(path)} && {cmd}; "
        f"_ec=$?; rm -f {shlex.quote(path)}; exit $_ec )"
    )


def _inline(cmd: str, env: dict[str, str]) -> str:
    """Prefix command with env assignments (fallback)."""
    prefix = " ".join(
        f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())
    )
    return f"{prefix} {cmd}"
```

- [ ] **Step 4: Run all script_env tests — expect PASS**

Run: `uv run pytest tests/matmaster/tools/test_script_env.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/script_env.py tests/matmaster/tools/test_script_env.py
git commit -m "feat: add inject/\_via_file/\_inline injection strategies"
```

---

## Task 3: Integrate into SkillTool + integration test

**Files:**
- Modify: `matmaster/tools/skill_tool.py:261-265`
- Modify: `tests/test_skill_tool.py`

- [ ] **Step 1: Write integration test**

Append to `tests/test_skill_tool.py` inside `TestRunScript`:

```python
    def test_injects_credentials_before_exec(self, tmp_path: Path) -> None:
        """run_script injects session credentials into the command."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "calc")
        _make_script(skill_dir, "run.py")
        registry = SkillRegistry(tmp_path)
        session = _mock_session()
        session._bohrium_credentials = {
            "access_key": "test_ak",
            "project_id": 123,
        }
        tool = SkillTool(registry, session)

        tool.execute({
            "skill_name": "calc",
            "action": "run_script",
            "script_name": "run.py",
        })

        # write_file should have been called with credential content
        session.write_file.assert_called_once()
        content = session.write_file.call_args[0][1]
        assert "BOHRIUM_ACCESS_KEY" in content

        # exec_bash called twice: chmod + actual command
        assert session.exec_bash.call_count == 2
        chmod_call = session.exec_bash.call_args_list[0][0][0]
        assert "chmod 600" in chmod_call
        run_call = session.exec_bash.call_args_list[1][0][0]
        assert "run.py" in run_call
        assert ". /tmp/.mm_env_" in run_call
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_skill_tool.py::TestRunScript::test_injects_credentials_before_exec -v`
Expected: FAIL — credentials not injected yet

- [ ] **Step 3: Edit `skill_tool.py` — add inject call**

In `matmaster/tools/skill_tool.py`, add import at top and one line in `_run_script`:

```python
# At line 261, after _build_command and before exec_bash:
from matmaster.tools.script_env import inject as inject_env

        cmd = self._build_command(
            script_path, project_root, script_args, self._session,
        )
        cmd = inject_env(cmd, self._session)

        result = self._session.exec_bash(cmd, timeout=script_timeout)
```

- [ ] **Step 4: Run full test suite — expect PASS**

Run: `uv run pytest tests/test_skill_tool.py tests/matmaster/tools/test_script_env.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/skill_tool.py tests/test_skill_tool.py
git commit -m "feat: inject session credentials into skill script execution"
```
