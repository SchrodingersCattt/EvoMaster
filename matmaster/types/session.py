"""Session Protocol and configuration models for matmaster.

Defines the structural typing contract that all session implementations
(local, SSH, Docker/tmux) must satisfy. Uses @runtime_checkable so that
isinstance() checks work without explicit inheritance.

SessionConfig / LocalSessionConfig / SSHSessionConfig are frozen Pydantic
models carrying construction-time parameters for each session variant.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class SessionConfig(BaseModel):
    """Base session configuration -- shared by all session types.

    frozen=True ensures config cannot be mutated after construction.
    Playground should pass the correct workspace_path at creation time.
    """

    model_config = ConfigDict(frozen=True)

    timeout: int = Field(default=300, description="Default execution timeout (seconds)")
    workspace_path: str = Field(default="/workspace", description="Workspace root path")
    working_dir: str = Field(
        default="/workspace", description="Working directory inside session"
    )


class LocalSessionConfig(SessionConfig):
    """Configuration for local subprocess sessions."""

    encoding: str = Field(default="utf-8", description="Default text encoding")


class SSHSessionConfig(SessionConfig):
    """Configuration for SSH remote sessions.

    Sensitive fields (password, key_data, passphrase) are masked in repr
    to prevent accidental leakage in logs.
    """

    host: str
    port: int = Field(default=22)
    username: str = Field(default="root")
    password: str | None = Field(default=None)
    key_file: str | None = Field(default=None)
    key_data: str | None = Field(default=None)
    passphrase: str | None = Field(default=None)
    connect_timeout: int = Field(default=10)
    keepalive_interval: int = Field(default=30)
    max_retries: int = Field(default=3)

    def __repr_args__(self):
        """Mask sensitive fields in repr output."""
        for k, v in super().__repr_args__():
            if k in ("password", "key_data", "passphrase") and v is not None:
                yield k, "***"
            else:
                yield k, v


@runtime_checkable
class Session(Protocol):
    """Session interface -- the structural typing contract for all session types.

    Any object implementing these 8 methods/properties satisfies the
    Session Protocol via duck typing. No explicit subclassing required.

    Methods mirror evomaster BaseSession's core interface, minus upload/download
    (those are environment-level concerns, not session-level).
    """

    @property
    def is_open(self) -> bool:
        """Whether the session is currently open."""
        ...

    def open(self) -> None:
        """Open the session, establishing connection if needed."""
        ...

    def close(self) -> None:
        """Close the session, releasing resources."""
        ...

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        stop_event: threading.Event | Any | None = None,
    ) -> dict[str, Any]:
        """Execute a bash command and return result dict."""
        ...

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """Read file content as string."""
        ...

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write string content to file."""
        ...

    def path_exists(self, path: str) -> bool:
        """Check if path exists."""
        ...

    def is_file(self, path: str) -> bool:
        """Check if path is a regular file."""
        ...
