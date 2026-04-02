"""Tests for matmaster.sessions.ssh.SSHSession.

All tests use mocked paramiko -- no real SSH connections needed.
"""

from __future__ import annotations

import stat as stat_module
from unittest.mock import MagicMock, patch

import pytest

from matmaster.types.session import Session, SSHSessionConfig


@pytest.fixture
def ssh_config():
    """Minimal SSHSessionConfig for testing."""
    return SSHSessionConfig(
        host="test-host",
        port=22,
        username="root",
        password="test-pass",
        workspace_path="/workspace",
        working_dir="/workspace",
    )


@pytest.fixture
def mock_paramiko():
    """Patch paramiko at the module level where SSHSession imports it."""
    with patch("matmaster.sessions.ssh.paramiko") as mock_pm:
        # Set up the mock SSHClient
        mock_client = MagicMock()
        mock_pm.SSHClient.return_value = mock_client
        mock_pm.AutoAddPolicy.return_value = MagicMock()

        # Transport mock
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport

        # SFTP mock (from transport.open_sftp_client for SFTPPool)
        mock_sftp = MagicMock()
        mock_sftp.stat.return_value = MagicMock()  # health check on release
        mock_transport.open_sftp_client.return_value = mock_sftp

        # SFTP file mock for file operations (reads via context manager)
        mock_sftp_file = MagicMock()
        mock_sftp_file.__enter__ = MagicMock(return_value=mock_sftp_file)
        mock_sftp_file.__exit__ = MagicMock(return_value=False)
        mock_sftp_file.read.return_value = b""
        mock_sftp.open.return_value = mock_sftp_file

        # exec_command mock (for _ssh_exec)
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        mock_channel = MagicMock()
        mock_channel.exit_status_ready.return_value = True
        mock_channel.recv_exit_status.return_value = 0
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b""
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        # PKey mock (avoid real RSAKey operations)
        mock_pm.RSAKey = MagicMock()
        mock_pm.PKey = MagicMock()

        yield {
            "paramiko": mock_pm,
            "client": mock_client,
            "transport": mock_transport,
            "sftp": mock_sftp,
            "stdout": mock_stdout,
            "stderr": mock_stderr,
            "channel": mock_channel,
        }


class TestSSHSessionLifecycle:
    """Lifecycle: init, open, close."""

    def test_initial_not_open(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        assert session.is_open is False

    def test_open_connects(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()
        assert session.is_open is True
        # Verify paramiko.SSHClient was constructed and connected
        mock_paramiko["paramiko"].SSHClient.assert_called()
        mock_paramiko["client"].connect.assert_called_once()

    def test_close_disconnects(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()
        assert session.is_open is True

        session.close()
        assert session.is_open is False
        mock_paramiko["client"].close.assert_called()


class TestSSHSessionFileOps:
    """File operations via mocked SFTP (through SFTPPool)."""

    def _make_open_session(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()
        return session

    def test_read_file(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        # Mock SFTP file read
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.read.return_value = b"hello content"
        mock_paramiko["sftp"].open.return_value = mock_file

        content = session.read_file("/remote/test.txt")
        assert content == "hello content"
        mock_paramiko["sftp"].open.assert_called_with("/remote/test.txt", "r")

    def test_write_file(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        # Mock SFTP file write
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_paramiko["sftp"].open.return_value = mock_file

        session.write_file("/remote/test.txt", "new content")
        mock_paramiko["sftp"].open.assert_called_with("/remote/test.txt", "w")
        mock_file.write.assert_called_once_with(b"new content")

    def test_path_exists_true(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        mock_paramiko["sftp"].stat.return_value = MagicMock()
        assert session.path_exists("/remote/existing") is True

    def test_path_exists_false(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        mock_paramiko["sftp"].stat.side_effect = FileNotFoundError
        assert session.path_exists("/remote/missing") is False

    def test_is_file_true(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        mock_stat = MagicMock()
        # S_IFREG is the regular file bit
        mock_stat.st_mode = stat_module.S_IFREG | 0o644
        mock_paramiko["sftp"].stat.return_value = mock_stat
        assert session.is_file("/remote/file.txt") is True

    def test_is_file_false_directory(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        mock_stat = MagicMock()
        mock_stat.st_mode = stat_module.S_IFDIR | 0o755
        mock_paramiko["sftp"].stat.return_value = mock_stat
        assert session.is_file("/remote/somedir") is False

    def test_is_file_false_not_found(self, ssh_config, mock_paramiko):
        session = self._make_open_session(ssh_config, mock_paramiko)

        mock_paramiko["sftp"].stat.side_effect = FileNotFoundError
        assert session.is_file("/remote/nope") is False


class TestSSHSessionProtocol:
    """SSHSession satisfies Session Protocol."""

    def test_satisfies_session_protocol(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        assert isinstance(session, Session)


class TestSSHSessionNotOpen:
    """Operations on closed session raise RuntimeError."""

    def test_exec_bash_not_open(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        with pytest.raises(RuntimeError, match="not open"):
            session.exec_bash("echo hi")

    def test_read_file_not_open(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        with pytest.raises(RuntimeError, match="not open"):
            session.read_file("/remote/file")

    def test_write_file_not_open(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        with pytest.raises(RuntimeError, match="not open"):
            session.write_file("/remote/file", "content")

    def test_path_exists_not_open(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        with pytest.raises(RuntimeError, match="not open"):
            session.path_exists("/remote/path")

    def test_is_file_not_open(self, ssh_config, mock_paramiko):
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        with pytest.raises(RuntimeError, match="not open"):
            session.is_file("/remote/file")


class TestSSHSessionExecBash:
    """Tests for the new exec_command-based exec_bash."""

    def test_simple_command(self, ssh_config, mock_paramiko):
        """exec_bash returns stdout, exit_code from channel."""
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()

        channel = MagicMock()
        recv_calls = [b"hello\n", b""]
        channel.recv_ready.side_effect = [True, False, False]
        channel.recv.side_effect = recv_calls
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.side_effect = [False, True]
        channel.recv_exit_status.return_value = 0
        mock_paramiko["transport"].open_session.return_value = channel

        result = session.exec_bash("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_timeout_returns_minus_one(self, ssh_config, mock_paramiko):
        """exec_bash returns exit_code=-1 on timeout."""
        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()

        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        mock_paramiko["transport"].open_session.return_value = channel

        result = session.exec_bash("sleep 999", timeout=0)
        assert result["exit_code"] == -1
        assert (
            "timed out" in result["stderr"].lower()
            or "timed out" in result["stdout"].lower()
        )

    def test_stop_event_cancels(self, ssh_config, mock_paramiko):
        """exec_bash returns exit_code=130 when stop_event is set."""
        import threading

        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()

        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        mock_paramiko["transport"].open_session.return_value = channel

        stop = threading.Event()
        stop.set()
        result = session.exec_bash("sleep 999", stop_event=stop)
        assert result["exit_code"] == 130

    def test_concurrent_exec_bash(self, ssh_config, mock_paramiko):
        """Multiple exec_bash calls run concurrently (no _prev_command_status block)."""
        import concurrent.futures

        from matmaster.sessions.ssh import SSHSession

        session = SSHSession(ssh_config)
        session.open()

        def make_channel():
            ch = MagicMock()
            ch.recv_ready.side_effect = [True, False, False]
            ch.recv.side_effect = [b"ok\n", b""]
            ch.recv_stderr_ready.return_value = False
            ch.exit_status_ready.side_effect = [False, True]
            ch.recv_exit_status.return_value = 0
            return ch

        mock_paramiko["transport"].open_session.side_effect = [
            make_channel(),
            make_channel(),
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(session.exec_bash, "cmd1")
            f2 = ex.submit(session.exec_bash, "cmd2")
            r1, r2 = f1.result(timeout=5), f2.result(timeout=5)
        assert r1["exit_code"] == 0
        assert r2["exit_code"] == 0
