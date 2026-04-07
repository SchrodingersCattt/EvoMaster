"""Tests for matmaster.tools.script_env — env injection bridge.

Credential resolution is tested in tests/matmaster/integration/test_runtime_bridge.py.
These tests focus on the injection mechanics: file-based wrapping, inline fallback,
and the inject_env / inject public API.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_with_runtime(**creds) -> MagicMock:
    from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
    from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext

    s = MagicMock()
    runtime = BohriumRuntimeHandle(
        credentials=BohriumCredentials(
            access_key=creds.get("access_key", "ak123"),
            project_id=creds.get("project_id", 456),
            user_id=creds.get("user_id", 7),
            user_no=creds.get("user_no", "U001"),
            base_url=creds.get("base_url", "https://openapi.test.dp.tech"),
        ),
        execution=BohriumExecutionContext(
            session_type="ssh",
            execution_workdir="/share",
            remote_workspace_root="/share",
            remote_project_root="/share/.matmaster",
            node_id=1,
            node_ip="10.0.0.1",
            ssh_attached=True,
        ),
        execution_session=s,
    )
    attach_runtime(s, runtime)
    return s


def _bare_session() -> MagicMock:
    s = MagicMock(spec=[])
    return s


# ---------------------------------------------------------------------------
# inject delegates to bridge tests
# ---------------------------------------------------------------------------


class TestInjectReadsRuntime:
    """Verify inject() reads env from the attached runtime handle."""

    def test_inject_reads_runtime_and_wraps(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_runtime(access_key="ak123", project_id=456)
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            return_value={"stdout": "", "stderr": "", "exit_code": 0}
        )

        result = inject("python run.py", session)
        assert result.startswith("( . ")
        assert "python run.py" in result

    def test_inject_noop_when_runtime_is_missing(self) -> None:
        from matmaster.tools.script_env import inject

        session = _bare_session()
        result = inject("python run.py", session)
        assert result == "python run.py"


# ---------------------------------------------------------------------------
# inject via file tests
# ---------------------------------------------------------------------------


class TestInjectViaFile:
    """Tests for file-based injection strategy."""

    def test_writes_file_and_wraps_command(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_runtime(access_key="ak123", project_id=456)
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            return_value={"stdout": "", "stderr": "", "exit_code": 0}
        )

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

        session = _session_with_runtime(access_key="ak")
        call_order = []
        session.write_file = MagicMock(
            side_effect=lambda *a: call_order.append("write")
        )
        session.exec_bash = MagicMock(
            side_effect=lambda *a, **kw: (
                call_order.append("chmod")
                or {"stdout": "", "stderr": "", "exit_code": 0}
            )
        )

        inject("cmd", session)
        assert call_order == ["write", "chmod"]


# ---------------------------------------------------------------------------
# inject fallback tests
# ---------------------------------------------------------------------------


class TestInjectFallback:
    """Tests for inline fallback when write_file fails."""

    def test_fallback_on_write_failure(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_runtime(access_key="ak123")
        session.write_file = MagicMock(side_effect=OSError("disk full"))

        result = inject("python run.py", session)
        assert "BOHRIUM_ACCESS_KEY=" in result
        assert "python run.py" in result
        assert not result.startswith("( . ")

    def test_fallback_on_chmod_failure(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_runtime(access_key="ak123")
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(side_effect=OSError("exec failed"))

        result = inject("python run.py", session)
        assert "BOHRIUM_ACCESS_KEY=" in result
        assert not result.startswith("( . ")


# ---------------------------------------------------------------------------
# inline format tests
# ---------------------------------------------------------------------------


class TestInjectInline:
    """Tests for inline prefix format."""

    def test_inline_format(self) -> None:
        from matmaster.tools.script_env import _inline

        env = {"BOHRIUM_ACCESS_KEY": "ak with space", "BOHRIUM_PROJECT_ID": "123"}
        result = _inline("python run.py", env)
        assert "BOHRIUM_ACCESS_KEY='ak with space'" in result
        assert "BOHRIUM_PROJECT_ID=" in result
        assert "123" in result
        assert result.endswith("python run.py")


# ---------------------------------------------------------------------------
# passthrough tests
# ---------------------------------------------------------------------------


class TestInjectPassthrough:
    """Tests for no-op when no credentials."""

    def test_no_creds_returns_unchanged(self) -> None:
        from matmaster.tools.script_env import inject

        session = _bare_session()
        assert inject("python run.py", session) == "python run.py"


# ---------------------------------------------------------------------------
# inject_env tests — explicit env dict API
# ---------------------------------------------------------------------------


class TestInjectEnv:
    """Tests for inject_env: explicit env dict injection."""

    def test_inject_uses_explicit_env_dict(self) -> None:
        from matmaster.tools.script_env import inject_env

        session = MagicMock()
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            return_value={"stdout": "", "stderr": "", "exit_code": 0}
        )

        result = inject_env("python run.py", {"BOHRIUM_ACCESS_KEY": "ak123"}, session)

        assert result.startswith("( . ")
        content_arg = session.write_file.call_args[0][1]
        assert "export BOHRIUM_ACCESS_KEY=" in content_arg

    def test_inject_env_noop_for_empty_env(self) -> None:
        from matmaster.tools.script_env import inject_env

        session = MagicMock()
        assert inject_env("python run.py", {}, session) == "python run.py"


class TestPrepareCommandHelpers:
    """Tests for the split inline/script preparation helpers."""

    def test_prepare_inline_command_uses_file_wrapping(self) -> None:
        from matmaster.tools.script_env import prepare_inline_command

        session = MagicMock()
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            return_value={"stdout": "", "stderr": "", "exit_code": 0}
        )

        result = prepare_inline_command(
            "echo hi", {"BOHRIUM_ACCESS_KEY": "ak"}, session
        )

        assert result.startswith("( . ")
        session.write_file.assert_called_once()
        session.exec_bash.assert_called_once()

    def test_prepare_script_command_writes_script_with_env(self) -> None:
        from matmaster.tools.script_env import prepare_script_command

        session = MagicMock()
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            return_value={"stdout": "", "stderr": "", "exit_code": 0}
        )

        result = prepare_script_command(
            "echo hi",
            {"BOHRIUM_ACCESS_KEY": "ak"},
            session,
            shell_path="bash",
        )

        written = session.write_file.call_args[0][1]
        assert "#!/usr/bin/env bash" in written
        assert "export BOHRIUM_ACCESS_KEY=" in written
        assert "echo hi" in written
        assert result.startswith("bash ")
