"""SSH Session -- matmaster native implementation.

Merges evomaster SSHSession + SSHEnv into a single class that directly
holds paramiko.SSHClient.  No BaseSession / BaseEnv inheritance.

Satisfies the Session Protocol (8 methods) via structural typing.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import stat
import tarfile
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from matmaster.sessions.tmux import PS1_PATTERN, BashMetadata
from matmaster.types.session import SSHSessionConfig

logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore[assignment]

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


class SSHSession:
    """SSH session -- direct paramiko client, tmux persistent shell.

    Implements the 8-method Session Protocol via duck typing:
    is_open, open, close, exec_bash, read_file, write_file, path_exists, is_file.

    Also exposes helper methods needed by external callers:
    ssh_exec, ssh_bash_noninteractive, upload_file, upload_directory_tarball.
    """

    def __init__(self, config: SSHSessionConfig) -> None:
        if paramiko is None:
            raise ImportError(
                "paramiko is required for SSHSession. "
                "Install with: pip install paramiko>=3.0"
            )

        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        # SSH / SFTP state
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._sftp_lock = threading.Lock()

        # tmux state
        self._tmux_session_name: str | None = None
        self._tmux_log_path: str | None = None

        # Session lifecycle
        self._is_open: bool = False
        self._lock = threading.Lock()

        # exec_bash tracking
        self._last_ps1_count: int = 0
        self._prev_command_status: Literal["completed", "timeout"] = "completed"

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

    # ------------------------------------------------------------------
    # Session Protocol: open / close
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Establish SSH connection and initialise tmux shell."""
        if self._is_open:
            self.logger.warning("Session already open")
            return

        self._connect()
        self._open_sftp()
        self._setup_tmux()

        # Capture initial PS1 count
        logs = self._get_tmux_logs()
        matches = list(PS1_PATTERN.finditer(logs))
        self._last_ps1_count = len(matches)

        self._is_open = True
        self.logger.info(
            "SSH session opened (%s:%s)", self.config.host, self.config.port
        )

    def close(self) -> None:
        """Close SSH connection and clean up tmux session."""
        if not self._is_open:
            return

        # Kill tmux session
        if self._tmux_session_name:
            try:
                self._ssh_exec(
                    f"tmux kill-session -t {self._tmux_session_name} 2>/dev/null || true"
                )
            except Exception as exc:
                self.logger.warning("Failed to kill tmux session: %s", exc)

        # Close SFTP
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

        # Close SSH client
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
        is_input: bool = False,
        stop_event: threading.Event | Any | None = None,
    ) -> dict[str, Any]:
        """Execute a bash command via tmux (persistent shell with state).

        Returns dict with: stdout, stderr, exit_code, working_dir, output.
        """
        if not self._is_open:
            raise RuntimeError("Session not open")

        timeout = timeout or self.config.timeout
        command = command.strip()

        # is_input mode: interact with a running command
        if is_input:
            if self._prev_command_status == "completed":
                if command == "":
                    return {
                        "stdout": "ERROR: No previous running command to retrieve logs from.",
                        "stderr": "",
                        "exit_code": 1,
                    }
                else:
                    return {
                        "stdout": "ERROR: No previous running command to interact with.",
                        "stderr": "",
                        "exit_code": 1,
                    }

            try:
                if command.startswith("C-") and len(command) == 3:
                    self._tmux_send_keys(command, enter=False)
                elif command == "":
                    pass
                else:
                    self._tmux_send_keys(command, enter=True)
            except (RuntimeError, OSError) as exc:
                return {
                    "stdout": f"[SSH error sending input: {exc}]",
                    "stderr": str(exc),
                    "exit_code": -1,
                    "output": f"[SSH error sending input: {exc}]",
                }
        else:
            # Normal command mode
            if self._prev_command_status != "completed" and command != "":
                return {
                    "stdout": "[Previous command is still running. Use is_input=true to interact.]",
                    "stderr": "",
                    "exit_code": 1,
                }

            if command != "":
                try:
                    self._tmux_send_keys(command, enter=True)
                except (RuntimeError, OSError) as exc:
                    return {
                        "stdout": f"[SSH error sending command: {exc}]",
                        "stderr": str(exc),
                        "exit_code": -1,
                        "output": f"[SSH error sending command: {exc}]",
                    }

        # Poll for completion via PS1 pattern
        start_time = time.time()
        poll_interval = 0.5
        self._prev_command_status = "timeout"
        _consecutive_failures = 0
        _MAX_CONSECUTIVE_FAILURES = 5
        interrupted = False
        interrupt_wait_until = 0.0

        while time.time() - start_time < timeout:
            # Handle stop_event cancellation
            if (
                stop_event is not None
                and getattr(stop_event, "is_set", None)
                and stop_event.is_set()
                and self._prev_command_status != "completed"
                and not interrupted
            ):
                try:
                    self._tmux_send_keys("C-c", enter=False)
                    interrupted = True
                    interrupt_wait_until = time.time() + 5.0
                except (RuntimeError, OSError) as exc:
                    self.logger.warning("failed to send C-c to tmux: %s", exc)

            try:
                logs = self._get_tmux_logs()
                _consecutive_failures = 0
            except (OSError, RuntimeError) as exc:
                _consecutive_failures += 1
                self.logger.warning(
                    "get_tmux_logs failed (%d/%d): %s",
                    _consecutive_failures,
                    _MAX_CONSECUTIVE_FAILURES,
                    exc,
                )
                if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    raise RuntimeError(
                        f"SSH connection appears broken after "
                        f"{_consecutive_failures} consecutive failures"
                    ) from exc
                time.sleep(poll_interval)
                continue

            matches = list(PS1_PATTERN.finditer(logs))
            ps1_count = len(matches)

            if ps1_count > self._last_ps1_count:
                self._prev_command_status = "completed"
                break

            if interrupted and time.time() >= interrupt_wait_until:
                break

            time.sleep(poll_interval)

        # Parse output
        logs = self._get_tmux_logs()
        matches = list(PS1_PATTERN.finditer(logs))
        ps1_count = len(matches)

        output = ""
        exit_code = -1
        working_dir = ""

        if ps1_count > self._last_ps1_count:
            if self._last_ps1_count > 0:
                prev_match = matches[self._last_ps1_count - 1]
                curr_match = matches[ps1_count - 1]
                output = logs[prev_match.end() : curr_match.start()]
            else:
                curr_match = matches[ps1_count - 1]
                output = logs[: curr_match.start()]

            try:
                metadata = BashMetadata.from_json(matches[-1].group(1))
                exit_code = metadata.exit_code
                working_dir = metadata.working_dir
            except Exception:
                pass

            self._last_ps1_count = ps1_count
        else:
            if self._last_ps1_count > 0 and matches:
                prev_match = matches[self._last_ps1_count - 1]
                output = logs[prev_match.end() :]

        # Clean ANSI codes and strip echoed command
        output = _ANSI_RE.sub("", output).strip()
        if command and output.startswith(command):
            output = output[len(command) :].strip()

        result: dict[str, Any] = {
            "stdout": output,
            "stderr": "",
            "exit_code": exit_code,
            "working_dir": working_dir,
            "output": output,
        }

        if self._prev_command_status == "timeout":
            result["stdout"] += f"\n[Command timed out after {timeout}s]"
            result["exit_code"] = -1
        elif interrupted and result.get("exit_code") == -1:
            result["stderr"] = "Command cancelled by stop request."
            result["exit_code"] = 130

        return result

    # ------------------------------------------------------------------
    # Session Protocol: file operations
    # ------------------------------------------------------------------

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """Read a remote text file via SFTP."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                with self._sftp.open(path, "r") as f:
                    raw = f.read()
                if isinstance(raw, bytes):
                    return raw.decode(encoding)
                return raw
            except FileNotFoundError:
                raise RuntimeError(f"File not found: {path}")

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write content to a remote text file via SFTP."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        assert self._sftp is not None

        remote_dir = str(PurePosixPath(path).parent)
        self._ssh_exec(f"mkdir -p '{remote_dir}'")
        with self._sftp_lock:
            with self._sftp.open(path, "w") as f:
                f.write(
                    content.encode(encoding) if isinstance(content, str) else content
                )

    def path_exists(self, path: str) -> bool:
        """Check if a remote path exists via SFTP stat."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                self._sftp.stat(path)
                return True
            except FileNotFoundError:
                return False

    def is_file(self, path: str) -> bool:
        """Check if a remote path is a regular file via SFTP stat."""
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                st = self._sftp.stat(path)
                return st.st_mode is not None and stat.S_ISREG(st.st_mode)
            except FileNotFoundError:
                return False

    # ------------------------------------------------------------------
    # Public helpers (non-Protocol, used by external callers)
    # ------------------------------------------------------------------

    def ssh_exec(
        self,
        command: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a single command over SSH (non-interactive, non-tmux).

        Public wrapper around _ssh_exec for external callers.
        """
        return self._ssh_exec(command, timeout=timeout)

    def ssh_bash_noninteractive(
        self,
        script: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Run a bash script over SSH without tmux.

        Encodes the script as base64 to avoid shell quoting issues with
        long one-liners that would fail via tmux send-keys.
        """
        self._ensure_connected()
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        wrapped = f"printf '%s' '{b64}' | base64 -d | bash -s"
        return self._ssh_exec(wrapped, timeout=timeout)

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a single local file to the remote host via SFTP."""
        t0 = time.monotonic()
        self._ensure_connected()
        assert self._sftp is not None
        self._ssh_exec(f"mkdir -p '{PurePosixPath(remote_path).parent}'")
        with self._sftp_lock:
            self._sftp.put(local_path, remote_path)
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
        assert self._sftp is not None

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
            with self._sftp_lock:
                self._sftp.put(tmp_path, remote_tmp)
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

    def _open_sftp(self) -> None:
        """Open (or re-open) a persistent SFTP channel."""
        if self._client is None:
            raise RuntimeError("SSH client not connected")
        self._sftp = self._client.open_sftp()

    def _ensure_connected(self) -> None:
        """Check the SSH connection and reconnect if broken."""
        alive = False
        if self._client is not None:
            transport = self._client.get_transport()
            alive = transport is not None and transport.is_active()

        if not alive:
            self.logger.warning("SSH connection lost, reconnecting...")
            self._connect()
            self._open_sftp()

    # ------------------------------------------------------------------
    # Internal: SSH command execution
    # ------------------------------------------------------------------

    def _ssh_exec(
        self,
        command: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a single command over SSH (non-interactive, non-tmux).

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
    # Internal: tmux management
    # ------------------------------------------------------------------

    def _setup_tmux(self) -> None:
        """Create a tmux session with PS1 prompt tracking."""
        session_name = f"mm-ssh-{int(time.time()) % 100000}"
        log_path = f"/tmp/{session_name}.log"

        self._tmux_session_name = session_name
        self._tmux_log_path = log_path

        # Ensure tmux is available
        check = self._ssh_exec("command -v tmux", timeout=10)
        if check.get("exit_code") != 0:
            self.logger.info("tmux not found, attempting to install...")
            self._ssh_exec(
                "(apt-get update -qq && apt-get install -y -qq tmux) || "
                "(yum install -y tmux) || "
                "(apk add --no-cache tmux)",
                timeout=120,
            )
            verify = self._ssh_exec("command -v tmux", timeout=10)
            if verify.get("exit_code") != 0:
                raise RuntimeError(
                    "tmux is not available on the remote node and auto-install failed. "
                    "Please ensure tmux is installed in the container image."
                )

        result = self._ssh_exec(f"tmux new-session -d -s {session_name} 'bash -i'")
        if result.get("exit_code") != 0:
            raise RuntimeError(
                f"Failed to create tmux session: "
                f"{result.get('stdout', '')} {result.get('stderr', '')}"
            )
        self._ssh_exec(f"tmux pipe-pane -o -t {session_name} 'cat >> {log_path}'")

        self._tmux_send_keys("bind-key -n Escape ''", enter=True)
        self._tmux_send_keys(
            "bind 'set enable-bracketed-paste off' 2>/dev/null; true", enter=True
        )

        ps1 = BashMetadata.to_ps1_prompt()
        init_cmd = f'PROMPT_COMMAND=\'PS1="{ps1}"\''
        self._tmux_send_keys(init_cmd, enter=True)
        self._tmux_send_keys("", enter=True)
        time.sleep(0.5)

        working_dir = self.config.working_dir
        self._tmux_send_keys(
            f"mkdir -p '{working_dir}' && cd '{working_dir}'", enter=True
        )
        time.sleep(0.2)

        self.logger.debug(
            "tmux session %s initialized at %s", session_name, working_dir
        )

    def _tmux_send_keys(self, keys: str, enter: bool = False) -> None:
        """Send keys to the tmux session.

        Raises RuntimeError if the tmux session no longer exists.
        """
        if not self._tmux_session_name:
            raise RuntimeError("tmux session not initialized")

        escaped = keys.replace("'", "'\\''")
        cmd = f"tmux send-keys -t {self._tmux_session_name} '{escaped}'"
        if enter:
            cmd += " C-m"
        result = self._ssh_exec(cmd)
        if result.get("exit_code", 0) != 0:
            raise RuntimeError(
                f"tmux send-keys failed (exit {result['exit_code']}): "
                f"{result.get('stderr', '').strip()}"
            )

    def _get_tmux_logs(self, timeout: float = 10.0) -> str:
        """Read the tmux log file via SFTP.

        Uses a daemon thread with join(timeout) to avoid hanging on slow
        SFTP reads.  Falls back to ssh_exec if SFTP times out.
        """
        if not self._tmux_log_path:
            return ""
        self._ensure_connected()
        assert self._sftp is not None

        container: dict[str, Any] = {}

        def _read() -> None:
            try:
                with self._sftp_lock:
                    with self._sftp.open(self._tmux_log_path, "r") as f:  # type: ignore[union-attr]
                        container["data"] = f.read().decode("utf-8", errors="replace")
            except FileNotFoundError:
                container["data"] = ""
            except Exception as exc:
                container["error"] = exc

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            self.logger.warning(
                "get_tmux_logs: SFTP read timed out after %.1fs, falling back to ssh_exec",
                timeout,
            )
            result = self._ssh_exec(
                f"cat {self._tmux_log_path} 2>/dev/null || echo ''",
                timeout=int(timeout),
            )
            return result.get("stdout", "")

        if "error" in container:
            self.logger.debug(
                "get_tmux_logs: SFTP error (%s), falling back to ssh_exec",
                container["error"],
            )
            result = self._ssh_exec(
                f"cat {self._tmux_log_path} 2>/dev/null || echo ''",
                timeout=int(timeout),
            )
            return result.get("stdout", "")

        return container.get("data", "")

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
