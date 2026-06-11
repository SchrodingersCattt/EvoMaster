"""Session Protocol and configuration models for matmaster.

Defines the structural typing contract that all session implementations
(local, SSH, Docker/tmux) must satisfy. Uses @runtime_checkable so that
isinstance() checks work without explicit inheritance.

SessionConfig / LocalSessionConfig / SSHSessionConfig are frozen Pydantic
models carrying construction-time parameters for each session variant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from matmaster.types.cancellation import CancellationToken
from matmaster.types.topology import SessionCapabilities

# Bohrium 远端 SSH 节点同时挂载 /share 与 /personal，二者均可作为会话工作目录。
# 这是双根工作目录的单一权威定义：会话目录校验与 bohrium_tool 远端路径识别均从此派生，
# 避免各处独立硬编码漂移。SQL CHECK 约束无法引用 Python 常量，靠
# create_bohrium_jobs_table.sql / 迁移脚本处的注释与此互指维持同步。
# 注意：本常量仅表达"哪些根可作为工作目录/远端路径"，不改变 agent 运行时对两个根的
# 可写性（workspace 落在哪个根即以该根为工作区）。
REMOTE_ACCESS_ROOTS: tuple[str, ...] = ("/share", "/personal")


@dataclass(frozen=True)
class SessionFileStat:
    size: int
    mtime: float


class SessionConfig(BaseModel):
    """Base session configuration -- shared by all session types.

    frozen=True ensures config cannot be mutated after construction.
    Playground should pass the correct workspace_path at creation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout: int = Field(default=300, description="Default execution timeout (seconds)")
    workspace_path: str = Field(default="/share", description="Workspace root path")


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

    Any object implementing these protocol members satisfies the
    Session Protocol via duck typing. No explicit subclassing required.

    Methods mirror the core runtime session interface. Raw file download is part
    of the session contract so higher layers can read bytes from local or remote
    filesystems without knowing the transport details.
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
        cancel_token: CancellationToken | None = None,
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

    def stat_file(self, path: str) -> SessionFileStat:
        """Return file size and mtime for semantic fingerprinting."""
        ...

    def download(self, path: str, timeout: int | None = None) -> bytes:
        """Read raw file bytes from the session filesystem."""
        ...

    def upload_directory(
        self,
        local_dir: str,
        remote_dir: str,
        exclude: set[str] | None = None,
    ) -> None:
        """Copy a local directory tree into the session filesystem."""
        ...

    @property
    def capabilities(self) -> SessionCapabilities:
        """Report runtime capabilities exposed by this session implementation."""
        ...
