"""Tests for Session.capabilities property implementations."""

from __future__ import annotations

import pytest

from matmaster.sessions.local import LocalSession
from matmaster.types.session import Session, SSHSessionConfig
from matmaster.types.topology import SessionCapabilities


class TestLocalCapabilities:
    def test_returns_session_capabilities(self, tmp_path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        caps = session.capabilities

        assert isinstance(caps, SessionCapabilities)

    def test_local_values(self, tmp_path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        caps = session.capabilities

        assert caps.shell_persistence == "stateless"
        assert caps.shell_input is False
        assert caps.file_ops == "native"
        assert caps.upload_support is False
        assert caps.exec_cancel is True


class TestSSHCapabilities:
    def test_ssh_values_without_connection(self) -> None:
        pytest.importorskip("paramiko")

        from matmaster.sessions.ssh import SSHSession

        config = SSHSessionConfig(host="dummy", port=22, username="test")
        session = SSHSession(config)
        caps = session.capabilities

        assert isinstance(caps, SessionCapabilities)
        assert caps.shell_persistence == "stateless"
        assert caps.shell_input is False
        assert caps.file_ops == "sftp"
        assert caps.upload_support is True
        assert caps.exec_cancel is True


class TestProtocolCompliance:
    def test_local_session_satisfies_protocol(self, tmp_path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        assert isinstance(session, Session)

    def test_ssh_session_satisfies_protocol(self) -> None:
        pytest.importorskip("paramiko")

        from matmaster.sessions.ssh import SSHSession

        config = SSHSessionConfig(host="dummy", port=22, username="test")
        assert isinstance(SSHSession(config), Session)
