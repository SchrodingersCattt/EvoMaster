"""SSH Session -- matmaster native implementation.

Merges evomaster SSHSession + SSHEnv into a single class that directly
holds paramiko.SSHClient.  No BaseSession / BaseEnv inheritance.

Satisfies the Session Protocol (8 methods) via structural typing.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shlex
import stat
import tarfile
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from matmaster.sessions.sftp_pool import SFTPPool
from matmaster.types.session import SSHSessionConfig
from matmaster.types.topology import SessionCapabilities

logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore[assignment]

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


class SSHSession:
    """SSH session -- direct paramiko client, exec_command per-channel.

    Implements the 8-method Session Protocol via duck typing:
    is_open, open, close, exec_bash, read_file, write_file, path_exists, is_file.

    Also exposes helper methods needed by external callers:
    ssh_exec, upload_file, upload_directory_tarball.
    """

    def __init__(self, config: SSHSessionConfig) -> None:
        if paramiko is None:
            raise ImportError(
                "paramiko is required for SSHSession. "
                "Install with: pip install paramiko>=3.0"
            )

        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        # SSH state
        self._client: paramiko.SSHClient | None = None
        self._connect_lock = threading.Lock()
        self._sftp_pool: SFTPPool | None = None
        self._workdir: str = config.working_dir

        # Session lifecycle
        self._is_open: bool = False
        self._lock = threading.Lock()

        # Public attributes for external callers (e.g. sync_skills_to_remote)
        self.remote_project_root: str | None = None
        self.remote_user_skills_root: str | None = None
        self.local_user_skills_root: str | None = None

    # ------------------------------------------------------------------
    # Session Protocol: is_open
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether the SSH session is currently open."""
        return self._is_open

    @property
    def capabilities(self) -> SessionCapabilities:
        """SSH session capabilities for ToolRunner/CapabilityPolicy."""
        return SessionCapabilities(
            shell_persistence="stateless",
            shell_input=False,
            file_ops="sftp",
            upload_support=True,
            exec_cancel=True,
        )

    # ------------------------------------------------------------------
    # Session Protocol: open / close
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Establish SSH connection and initialise SFTP pool."""
        if self._is_open:
            self.logger.warning("Session already open")
            return

        self._connect()
        self._sftp_pool = SFTPPool(self._client.get_transport())
        self._ssh_exec(f"mkdir -p {shlex.quote(self._workdir)}")
        self._is_open = True
        self.logger.info(
            "SSH session opened (%s:%s)", self.config.host, self.config.port
        )

    def close(self) -> None:
        """Close SSH connection and clean up SFTP pool."""
        if not self._is_open:
            return

        if self._sftp_pool is not None:
            self._sftp_pool.close_all()
            self._sftp_pool = None

        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        self._is_open = False
        self.logger.info("SSH session closed")

    # ------------------------------------------------------------------
    # Session Protocol: exec_bash
    # ------------------------------------------------------------------

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        stop_event: threading.Event | Any | None = None,
    ) -> dict[str, Any]:
        """Execute a bash command via exec_command per-channel with streaming reads.

        Returns dict with: stdout, stderr, exit_code, working_dir, output.
        """
        if not self._is_open:
            raise RuntimeError("Session not open")

        self._ensure_connected()
        timeout = timeout if timeout is not None else self.config.timeout

        transport = self._client.get_transport()
        channel = transport.open_session()
        wrapped = (
            f"bash -l -c {shlex.quote(f'cd {shlex.quote(self._workdir)} && {command}')}"
        )
        channel.exec_command(wrapped)

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        deadline = time.monotonic() + timeout

        while not channel.exit_status_ready():
            if channel.recv_ready():
                stdout_chunks.append(channel.recv(65536))
            if channel.recv_stderr_ready():
                stderr_chunks.append(channel.recv_stderr(65536))
            if time.monotonic() >= deadline:
                channel.close()
                out = b"".join(stdout_chunks).decode("utf-8", errors="replace")
                return {
                    "stdout": out,
                    "stderr": f"Command timed out after {timeout}s",
                    "exit_code": -1,
                    "working_dir": self._workdir,
                    "output": out + f"\nCommand timed out after {timeout}s",
                }
            is_set = getattr(stop_event, "is_set", None)
            if callable(is_set) and is_set():
                channel.close()
                out = b"".join(stdout_chunks).decode("utf-8", errors="replace")
                return {
                    "stdout": out,
                    "stderr": "Command cancelled.",
                    "exit_code": 130,
                    "working_dir": self._workdir,
                    "output": out + "\nCommand cancelled.",
                }
            time.sleep(0.05)

        # drain remaining
        while channel.recv_ready():
            stdout_chunks.append(channel.recv(65536))
        while channel.recv_stderr_ready():
            stderr_chunks.append(channel.recv_stderr(65536))

        exit_code = channel.recv_exit_status()
        out = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        err = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        return {
            "stdout": out,
            "stderr": err,
            "exit_code": exit_code,
            "working_dir": self._workdir,
            "output": out + err,
        }

    # ------------------------------------------------------------------
    # Session Protocol: file operations
    # ------------------------------------------------------------------

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """Read a remote text file via SFTP pool."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        pool = self._sftp_pool
        sftp = pool.acquire()
        try:
            with sftp.open(path, "r") as f:
                raw = f.read()
            return raw.decode(encoding) if isinstance(raw, bytes) else raw
        except FileNotFoundError:
            raise RuntimeError(f"File not found: {path}")
        finally:
            pool.release(sftp)

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write content to a remote text file via SFTP pool."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()

        remote_dir = str(PurePosixPath(path).parent)
        self._ssh_exec(f"mkdir -p '{remote_dir}'")
        pool = self._sftp_pool
        sftp = pool.acquire()
        try:
            with sftp.open(path, "w") as f:
                f.write(
                    content.encode(encoding) if isinstance(content, str) else content
                )
        finally:
            pool.release(sftp)

    def path_exists(self, path: str) -> bool:
        """Check if a remote path exists via SFTP stat."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        pool = self._sftp_pool
        sftp = pool.acquire()
        try:
            sftp.stat(path)
            return True
        except FileNotFoundError:
            return False
        finally:
            pool.release(sftp)

    def is_file(self, path: str) -> bool:
        """Check if a remote path is a regular file via SFTP stat."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        pool = self._sftp_pool
        sftp = pool.acquire()
        try:
            st = sftp.stat(path)
            return st.st_mode is not None and stat.S_ISREG(st.st_mode)
        except FileNotFoundError:
            return False
        finally:
            pool.release(sftp)

    # ------------------------------------------------------------------
    # Public helpers (non-Protocol, used by external callers)
    # ------------------------------------------------------------------

    def ssh_exec(
        self,
        command: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a single command over SSH (non-interactive).

        Public wrapper around _ssh_exec for external callers.
        """
        return self._ssh_exec(command, timeout=timeout)

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a single local file to the remote host via SFTP pool."""
        t0 = time.monotonic()
        self._ensure_connected()
        self._ssh_exec(f"mkdir -p '{PurePosixPath(remote_path).parent}'")
        pool = self._sftp_pool
        sftp = pool.acquire()
        try:
            sftp.put(local_path, remote_path)
        finally:
            pool.release(sftp)
        logger.info(
            "upload_file: %s -> %s elapsed=%.2fs",
            local_path,
            remote_path,
            time.monotonic() - t0,
        )

    def upload_directory_tarball(
        self,
        local_dir: str,
        remote_dir: str,
        exclude: set[str] | None = None,
    ) -> int:
        """Upload a local directory as a single tarball (pack -> put -> extract).

        Much faster than per-file SFTP puts for large trees.
        Returns the number of files uploaded.
        """
        self._ensure_connected()

        local_root = Path(local_dir)
        if not local_root.is_dir():
            raise FileNotFoundError(f"Local directory not found: {local_dir}")

        t0 = time.monotonic()
        entries = self._walk_filtered(local_root, exclude or set())

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for abs_path, arcname in entries:
                tar.add(str(abs_path), arcname=arcname)
        tar_bytes = buf.getvalue()
        logger.info(
            "upload_directory_tarball: packed %d files (%.1f KB) in %.2fs",
            len(entries),
            len(tar_bytes) / 1024,
            time.monotonic() - t0,
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
            tmp.write(tar_bytes)
            tmp_path = tmp.name

        remote_tmp = f"/tmp/_mm_upload_{int(time.monotonic() * 1000)}.tar.gz"
        try:
            t1 = time.monotonic()
            pool = self._sftp_pool
            sftp = pool.acquire()
            try:
                sftp.put(tmp_path, remote_tmp)
            finally:
                pool.release(sftp)
            logger.info(
                "upload_directory_tarball: sftp.put %.2fs", time.monotonic() - t1
            )

            t2 = time.monotonic()
            self._ssh_exec(
                f"mkdir -p '{remote_dir}' && tar -xzf '{remote_tmp}' -C '{remote_dir}'"
            )
            logger.info(
                "upload_directory_tarball: extract %.2fs", time.monotonic() - t2
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            self._ssh_exec(f"rm -f '{remote_tmp}'")

        logger.info(
            "upload_directory_tarball: %s -> %s (%d files) total=%.2fs",
            local_dir,
            remote_dir,
            len(entries),
            time.monotonic() - t0,
        )
        return len(entries)

    # ------------------------------------------------------------------
    # Internal: SSH connection management
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Establish (or re-establish) the SSH connection with retry."""
        cfg = self.config
        host = cfg.host
        port = cfg.port
        username = cfg.username
        password = cfg.password
        key_file = cfg.key_file
        key_data = cfg.key_data
        passphrase = cfg.passphrase
        connect_timeout = cfg.connect_timeout
        keepalive_interval = cfg.keepalive_interval
        max_retries = cfg.max_retries

        if not host:
            raise ValueError("SSH host is required")

        # Resolve private key
        pkey: paramiko.PKey | None = None
        if key_data:
            pkey = paramiko.RSAKey.from_private_key(
                io.StringIO(key_data), password=passphrase
            )
        elif key_file:
            pkey = paramiko.RSAKey.from_private_key_file(
                os.path.expanduser(key_file), password=passphrase
            )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "hostname": host,
                    "port": port,
                    "username": username,
                    "timeout": connect_timeout,
                    "allow_agent": False,
                    "look_for_keys": False,
                }
                if pkey is not None:
                    kwargs["pkey"] = pkey
                elif password:
                    kwargs["password"] = password
                client.connect(**kwargs)
                self.logger.info(
                    "SSH connected to %s:%s (attempt %d)", host, port, attempt
                )
                break
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "SSH connect attempt %d/%d failed: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(min(2**attempt, 10))
        else:
            raise ConnectionError(
                f"Failed to connect to {host}:{port} after {max_retries} attempts"
            ) from last_exc

        transport = client.get_transport()
        if transport is not None and keepalive_interval > 0:
            transport.set_keepalive(keepalive_interval)

        self._client = client

    def _ensure_connected(self) -> None:
        """Check the SSH connection and reconnect if broken (double-check locking)."""
        if (
            self._client
            and self._client.get_transport()
            and self._client.get_transport().is_active()
        ):
            return
        with self._connect_lock:
            if (
                self._client
                and self._client.get_transport()
                and self._client.get_transport().is_active()
            ):
                return
            self.logger.warning("SSH connection lost, reconnecting...")
            self._connect()
            old_pool = self._sftp_pool
            if old_pool:
                old_pool.close_all()
            self._sftp_pool = SFTPPool(self._client.get_transport())

    # ------------------------------------------------------------------
    # Internal: SSH command execution
    # ------------------------------------------------------------------

    def _ssh_exec(
        self,
        command: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a single command over SSH (non-interactive).

        Network/connection exceptions are propagated to callers.
        """
        self._ensure_connected()
        assert self._client is not None

        timeout = timeout or self.config.timeout

        _stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        channel = stdout.channel
        channel.settimeout(timeout)

        deadline = time.monotonic() + timeout
        while not channel.exit_status_ready():
            if time.monotonic() > deadline:
                channel.close()
                raise TimeoutError(
                    f"SSH command timed out after {timeout}s: {command[:120]}"
                )
            time.sleep(0.1)

        exit_code = channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return {
            "stdout": out,
            "stderr": err,
            "exit_code": exit_code,
            "output": out + err,
        }

    # ------------------------------------------------------------------
    # Internal: tarball helper
    # ------------------------------------------------------------------

    @staticmethod
    def _walk_filtered(local_root: Path, exclude: set[str]) -> list[tuple[Path, str]]:
        """Walk *local_root* and return (abs_path, arcname) pairs,
        skipping directories and files whose names are in *exclude*."""
        result: list[tuple[Path, str]] = []
        for root, dirs, files in os.walk(local_root):
            dirs[:] = [d for d in dirs if d not in exclude]
            for fname in files:
                if fname in exclude:
                    continue
                abs_path = Path(root) / fname
                arcname = abs_path.relative_to(local_root).as_posix()
                result.append((abs_path, arcname))
        return result
