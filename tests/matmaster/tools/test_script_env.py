"""Tests for matmaster.tools.script_env — credential-to-env bridge."""

from __future__ import annotations

from unittest.mock import MagicMock

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
# _collect tests
# ---------------------------------------------------------------------------


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
