"""Tests for matmaster.tools.script_env — env injection bridge.

Credential resolution is tested in tests/matmaster/integration/test_runtime_bridge.py.
These tests focus on the injection mechanics: file-based wrapping, inline fallback,
and the inject_env / inject public API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_with_creds(**creds) -> MagicMock:
    s = MagicMock()
    s._bohrium_credentials = creds
    return s


def _bare_session() -> MagicMock:
    s = MagicMock(spec=[])
    return s


# ---------------------------------------------------------------------------
# inject delegates to bridge tests
# ---------------------------------------------------------------------------


class TestInjectDelegatesToBridge:
    """Verify inject() calls build_service_env and delegates to inject_env."""

    def test_inject_calls_bridge_and_wraps(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak123", project_id=456)
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            return_value={"stdout": "", "stderr": "", "exit_code": 0}
        )

        with patch(
            "matmaster.integration.runtime_bridge.build_service_env",
            return_value={"BOHRIUM_ACCESS_KEY": "ak123", "BOHRIUM_PROJECT_ID": "456"},
        ) as mock_build:
            result = inject("python run.py", session)
            mock_build.assert_called_once_with("bohrium", session=session)

        assert result.startswith("( . ")
        assert "python run.py" in result

    def test_inject_noop_when_bridge_returns_empty(self) -> None:
        from matmaster.tools.script_env import inject

        session = _bare_session()

        with patch(
            "matmaster.integration.runtime_bridge.build_service_env",
            return_value={},
        ):
            result = inject("python run.py", session)

        assert result == "python run.py"


# ---------------------------------------------------------------------------
# inject via file tests
# ---------------------------------------------------------------------------


class TestInjectViaFile:
    """Tests for file-based injection strategy."""

    def test_writes_file_and_wraps_command(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak123", project_id=456)
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

        session = _session_with_creds(access_key="ak")
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

        session = _session_with_creds(access_key="ak123")
        session.write_file = MagicMock(side_effect=OSError("disk full"))

        result = inject("python run.py", session)
        assert "BOHRIUM_ACCESS_KEY=" in result
        assert "python run.py" in result
        assert not result.startswith("( . ")

    def test_fallback_on_chmod_failure(self) -> None:
        from matmaster.tools.script_env import inject

        session = _session_with_creds(access_key="ak123")
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
