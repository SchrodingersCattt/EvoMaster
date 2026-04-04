"""Tests for matmaster.types.session Protocol and Config models + tmux helpers."""

from __future__ import annotations

import json
from typing import get_type_hints

import pytest

from matmaster.sessions.local import LocalSession
from matmaster.sessions.tmux import PS1_BEGIN, PS1_END, PS1_PATTERN, BashMetadata
from matmaster.types.cancellation import CancellationToken
from matmaster.types.session import (
    LocalSessionConfig,
    Session,
    SessionConfig,
    SSHSessionConfig,
)
from matmaster.types.topology import SessionCapabilities


class TestSessionProtocol:
    """Session Protocol isinstance checks."""

    def test_local_session_satisfies_protocol(self, tmp_path) -> None:
        """Test 1: isinstance(LocalSession(...), Session) returns True."""
        session = LocalSession(workspace_path=tmp_path)
        assert isinstance(session, Session)

    def test_mock_with_all_methods_satisfies_protocol(self) -> None:
        """Test 8: Any object implementing 8 methods satisfies Session."""

        class FakeSession:
            @property
            def is_open(self) -> bool:
                return True

            @property
            def capabilities(self) -> SessionCapabilities:
                return SessionCapabilities()

            def open(self) -> None: ...
            def close(self) -> None: ...
            def exec_bash(self, command, timeout=None, cancel_token=None): ...
            def read_file(self, path, encoding="utf-8"): ...
            def write_file(self, path, content, encoding="utf-8"): ...
            def path_exists(self, path): ...
            def is_file(self, path): ...

        assert isinstance(FakeSession(), Session)

    def test_exec_bash_uses_cancel_token_hint(self) -> None:
        hints = get_type_hints(Session.exec_bash)

        assert "cancel_token" in hints
        assert hints["cancel_token"] == CancellationToken | None
        assert all(not name.endswith("_event") for name in hints)


class TestSessionConfig:
    """SessionConfig frozen Pydantic model."""

    def test_session_config_defaults(self) -> None:
        """Test 2: SessionConfig has timeout/workspace_path/working_dir fields."""
        cfg = SessionConfig()
        assert cfg.timeout == 300
        assert cfg.workspace_path == "/workspace"
        assert cfg.working_dir == "/workspace"

    def test_session_config_frozen(self) -> None:
        """Test 2 (continued): SessionConfig is frozen."""
        cfg = SessionConfig()
        with pytest.raises((TypeError, ValueError)):  # ValidationError for frozen model
            cfg.timeout = 999


class TestLocalSessionConfig:
    """LocalSessionConfig extends SessionConfig."""

    def test_encoding_default(self) -> None:
        """Test 3: LocalSessionConfig has encoding field defaulting to utf-8."""
        cfg = LocalSessionConfig()
        assert cfg.encoding == "utf-8"

    def test_inherits_session_config(self) -> None:
        """Test 3 (continued): LocalSessionConfig inherits SessionConfig fields."""
        cfg = LocalSessionConfig(timeout=60, workspace_path="/work")
        assert cfg.timeout == 60
        assert cfg.workspace_path == "/work"
        assert cfg.encoding == "utf-8"


class TestSSHSessionConfig:
    """SSHSessionConfig extends SessionConfig with SSH-specific fields."""

    def test_ssh_fields(self) -> None:
        """Test 4: SSHSessionConfig has all required SSH fields."""
        cfg = SSHSessionConfig(host="10.0.0.1")
        assert cfg.host == "10.0.0.1"
        assert cfg.port == 22
        assert cfg.username == "root"
        assert cfg.password is None
        assert cfg.key_file is None
        assert cfg.key_data is None
        assert cfg.passphrase is None
        assert cfg.connect_timeout == 10
        assert cfg.keepalive_interval == 30
        assert cfg.max_retries == 3

    def test_ssh_repr_hides_secrets(self) -> None:
        """Test 5: SSHSessionConfig repr hides password/key_data/passphrase."""
        cfg = SSHSessionConfig(
            host="10.0.0.1",
            password="secret123",
            key_data="private-key-data",
            passphrase="my-passphrase",
        )
        r = repr(cfg)
        assert "secret123" not in r
        assert "private-key-data" not in r
        assert "my-passphrase" not in r
        assert "***" in r


class TestBashMetadata:
    """BashMetadata from tmux module."""

    def test_from_json_parses_correctly(self) -> None:
        """Test 6: BashMetadata.from_json parses exit_code/working_dir/pid."""
        data = json.dumps({"exit_code": "0", "working_dir": "/workspace", "pid": "42"})
        meta = BashMetadata.from_json(data)
        assert meta.exit_code == 0
        assert meta.working_dir == "/workspace"
        assert meta.pid == 42

    def test_from_json_defaults_on_bad_input(self) -> None:
        """BashMetadata.from_json returns defaults on invalid JSON."""
        meta = BashMetadata.from_json("not json")
        assert meta.exit_code == -1
        assert meta.working_dir == ""
        assert meta.pid == -1


class TestPS1Pattern:
    """PS1 pattern matching from tmux module."""

    def test_ps1_pattern_matches(self) -> None:
        """Test 7: PS1_PATTERN matches content between PS1_BEGIN and PS1_END."""
        payload = '{"exit_code": "0", "working_dir": "/tmp", "pid": "1"}'
        text = f"some output{PS1_BEGIN.strip()}{payload}{PS1_END.strip()}more output"
        match = PS1_PATTERN.search(text)
        assert match is not None
        assert payload in match.group(1)
