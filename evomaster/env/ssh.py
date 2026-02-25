"""SSH 环境实现

通过 paramiko 连接远端容器（如 Bohrium 节点），提供命令执行和文件操作。
容器生命周期由外部后端管理，SSHEnv 只负责「连上去、用、断开」。
"""

from __future__ import annotations

import io
import logging
import os
import stat
import tarfile
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field

from evomaster.agent.session.base import SessionConfig

from .base import BaseEnv, EnvConfig
from .docker import BashMetadata

logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore[assignment]


class SSHEnvConfig(EnvConfig):
    """SSH 环境配置"""

    session_config: SessionConfig = Field(
        ..., description='Session 配置（SSHSessionConfig）'
    )


class SSHEnv(BaseEnv):
    """SSH 环境实现

    通过 paramiko 连接远端容器，提供：
    - SSH 命令执行
    - tmux 持久 shell（复用 DockerEnv 的 PS1+tmux 机制）
    - SFTP 文件操作
    - 自动心跳与断线重连
    """

    def __init__(self, config: SSHEnvConfig | None = None):
        if config is None:
            raise ValueError('SSHEnv requires SSHEnvConfig with session_config')
        if paramiko is None:
            raise ImportError(
                'paramiko is required for SSHEnv. Install with: pip install paramiko>=3.0'
            )
        super().__init__(config)
        self.config: SSHEnvConfig = config
        self._ssh_client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._sftp_lock = threading.Lock()
        self._tmux_session: str | None = None
        self._tmux_log_path: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """建立 SSH 连接并初始化 tmux 持久 shell。"""
        if self._is_ready:
            self.logger.warning('Environment already setup')
            return

        self.logger.info('Setting up SSH environment')
        self._connect()
        self._open_sftp()
        self._setup_tmux()
        self._is_ready = True
        self.logger.info('SSH environment setup complete')

    def teardown(self) -> None:
        """关闭 SSH 连接（不关闭远端容器——后端负责）。"""
        if not self._is_ready:
            return

        self.logger.info('Tearing down SSH environment')

        if self._tmux_session:
            try:
                self.ssh_exec(
                    f"tmux kill-session -t {self._tmux_session} 2>/dev/null || true"
                )
            except Exception as exc:
                self.logger.warning('Failed to kill tmux session: %s', exc)

        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

        if self._ssh_client is not None:
            try:
                self._ssh_client.close()
            except Exception:
                pass
            self._ssh_client = None

        self._is_ready = False
        self.logger.info('SSH environment teardown complete')

    # Stubbed BaseEnv abstract methods (not used for SSH)
    def get_session(self) -> Any:
        raise NotImplementedError('SSHEnv does not provide session directly')

    def submit_job(self, command: str, job_type: str = 'debug', **kwargs: Any) -> str:
        raise NotImplementedError('SSHEnv does not support job submission')

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        raise NotImplementedError('SSHEnv does not support job status')

    def cancel_job(self, job_id: str) -> None:
        raise NotImplementedError('SSHEnv does not support job cancellation')

    # ------------------------------------------------------------------
    # SSH connection management
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Establish (or re-establish) the SSH connection."""
        cfg = self.config.session_config
        host: str = getattr(cfg, 'host', '')
        port: int = getattr(cfg, 'port', 22)
        username: str = getattr(cfg, 'username', 'root')
        password: str | None = getattr(cfg, 'password', None)
        key_file: str | None = getattr(cfg, 'key_file', None)
        key_data: str | None = getattr(cfg, 'key_data', None)
        passphrase: str | None = getattr(cfg, 'passphrase', None)
        connect_timeout: int = getattr(cfg, 'connect_timeout', 10)
        keepalive_interval: int = getattr(cfg, 'keepalive_interval', 30)
        max_retries: int = getattr(cfg, 'max_retries', 3)

        if not host:
            raise ValueError('SSH host is required')

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
                    'hostname': host,
                    'port': port,
                    'username': username,
                    'timeout': connect_timeout,
                    'allow_agent': False,
                    'look_for_keys': False,
                }
                if pkey is not None:
                    kwargs['pkey'] = pkey
                elif password:
                    kwargs['password'] = password
                client.connect(**kwargs)
                self.logger.info(
                    'SSH connected to %s:%s (attempt %d)', host, port, attempt
                )
                break
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    'SSH connect attempt %d/%d failed: %s', attempt, max_retries, exc
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

        self._ssh_client = client

    def _open_sftp(self) -> None:
        """Open (or re-open) a persistent SFTP channel."""
        if self._ssh_client is None:
            raise RuntimeError('SSH client not connected')
        self._sftp = self._ssh_client.open_sftp()

    def _ensure_connected(self) -> None:
        """Check the SSH connection and reconnect if broken."""
        alive = False
        if self._ssh_client is not None:
            transport = self._ssh_client.get_transport()
            alive = transport is not None and transport.is_active()

        if not alive:
            self.logger.warning('SSH connection lost, reconnecting...')
            self._connect()
            self._open_sftp()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def ssh_exec(
        self,
        command: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a single command over SSH (non-interactive, non-tmux).

        Analogous to ``DockerEnv.docker_exec``.

        Network/connection exceptions (socket.timeout, SSHException, OSError) are
        propagated to the caller instead of being swallowed, so upper layers can
        detect a broken connection rather than hanging forever.
        """
        self._ensure_connected()
        assert self._ssh_client is not None

        timeout = timeout or self.config.session_config.timeout

        _stdin, stdout, stderr = self._ssh_client.exec_command(
            command, timeout=timeout
        )
        channel = stdout.channel
        channel.settimeout(timeout)

        deadline = time.monotonic() + timeout
        while not channel.exit_status_ready():
            if time.monotonic() > deadline:
                channel.close()
                raise TimeoutError(
                    f'SSH command timed out after {timeout}s: {command[:120]}'
                )
            time.sleep(0.1)

        exit_code = channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        return {
            'stdout': out,
            'stderr': err,
            'exit_code': exit_code,
            'output': out + err,
        }

    # ------------------------------------------------------------------
    # tmux (same mechanism as DockerEnv)
    # ------------------------------------------------------------------

    def _setup_tmux(self) -> None:
        """Create a tmux session with PS1 prompt tracking."""
        session_name = f"evo-ssh-{int(time.time()) % 100000}"
        log_path = f"/tmp/{session_name}.log"

        self._tmux_session = session_name
        self._tmux_log_path = log_path

        check = self.ssh_exec('command -v tmux', timeout=10)
        if check.get('exit_code') != 0:
            self.logger.info('tmux not found, attempting to install...')
            self.ssh_exec(
                '(apt-get update -qq && apt-get install -y -qq tmux) || '
                '(yum install -y tmux) || '
                '(apk add --no-cache tmux)',
                timeout=120,
            )
            verify = self.ssh_exec('command -v tmux', timeout=10)
            if verify.get('exit_code') != 0:
                raise RuntimeError(
                    'tmux is not available on the remote node and auto-install failed. '
                    'Please ensure tmux is installed in the container image.'
                )

        result = self.ssh_exec(f"tmux new-session -d -s {session_name} 'bash -i'")
        if result.get('exit_code') != 0:
            raise RuntimeError(
                f"Failed to create tmux session: {result.get('stdout', '')} {result.get('stderr', '')}"
            )
        self.ssh_exec(f"tmux pipe-pane -o -t {session_name} 'cat >> {log_path}'")

        self.tmux_send_keys("bind-key -n Escape ''", enter=True)
        self.tmux_send_keys(
            "bind 'set enable-bracketed-paste off' 2>/dev/null; true", enter=True
        )

        ps1 = BashMetadata.to_ps1_prompt()
        init_cmd = f'PROMPT_COMMAND=\'PS1="{ps1}"\''
        self.tmux_send_keys(init_cmd, enter=True)
        self.tmux_send_keys('', enter=True)
        time.sleep(0.5)

        working_dir = getattr(self.config.session_config, 'working_dir', '/workspace')
        self.tmux_send_keys(
            f"mkdir -p '{working_dir}' && cd '{working_dir}'", enter=True
        )
        time.sleep(0.2)

        self.logger.debug(
            'tmux session %s initialized at %s', session_name, working_dir
        )

    def tmux_send_keys(self, keys: str, enter: bool = False) -> None:
        """Send keys to the tmux session.

        Raises ``RuntimeError`` if the tmux session no longer exists (e.g. the
        remote process crashed), so callers are not left polling for a PS1
        prompt that will never arrive.
        """
        if not self._tmux_session:
            raise RuntimeError('tmux session not initialized')

        escaped = keys.replace("'", "'\\''")
        cmd = f"tmux send-keys -t {self._tmux_session} '{escaped}'"
        if enter:
            cmd += ' C-m'
        result = self.ssh_exec(cmd)
        if result.get('exit_code', 0) != 0:
            raise RuntimeError(
                f"tmux send-keys failed (exit {result['exit_code']}): "
                f"{result.get('stderr', '').strip()}"
            )

    def get_tmux_logs(self, timeout: float = 10.0) -> str:
        """Read the tmux log file via SFTP (fast, no exec overhead).

        SFTP channels have no native per-operation timeout, so the read is
        wrapped in a daemon thread with ``join(timeout)``.  If the thread
        does not finish in time the method falls back to ``ssh_exec`` (which
        has its own deadline), keeping the caller from hanging indefinitely.
        """
        if not self._tmux_log_path:
            return ''
        self._ensure_connected()
        assert self._sftp is not None

        container: dict[str, Any] = {}

        def _read() -> None:
            try:
                with self._sftp_lock:
                    with self._sftp.open(self._tmux_log_path, 'r') as f:  # type: ignore[union-attr]
                        container['data'] = f.read().decode('utf-8', errors='replace')
            except FileNotFoundError:
                container['data'] = ''
            except Exception as exc:
                container['error'] = exc

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            self.logger.warning(
                'get_tmux_logs: SFTP read timed out after %.1fs, falling back to ssh_exec',
                timeout,
            )
            result = self.ssh_exec(
                f"cat {self._tmux_log_path} 2>/dev/null || echo ''",
                timeout=int(timeout),
            )
            return result.get('stdout', '')

        if 'error' in container:
            self.logger.debug(
                'get_tmux_logs: SFTP error (%s), falling back to ssh_exec',
                container['error'],
            )
            result = self.ssh_exec(
                f"cat {self._tmux_log_path} 2>/dev/null || echo ''",
                timeout=int(timeout),
            )
            return result.get('stdout', '')

        return container.get('data', '')

    # ------------------------------------------------------------------
    # File operations (SFTP)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # File upload helpers
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a single local file to the remote host via SFTP."""
        t0 = time.monotonic()
        self._ensure_connected()
        assert self._sftp is not None
        self.ssh_exec(f"mkdir -p '{PurePosixPath(remote_path).parent}'")
        with self._sftp_lock:
            self._sftp.put(local_path, remote_path)
        logger.info(
            'upload_file: %s -> %s elapsed=%.2fs',
            local_path,
            remote_path,
            time.monotonic() - t0,
        )

    @staticmethod
    def _walk_filtered(
        local_root: Path, exclude: set[str]
    ) -> list[tuple[Path, str]]:
        """Walk *local_root* and return ``(abs_path, arcname)`` pairs,
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

    def upload_directory(
        self,
        local_dir: str,
        remote_dir: str,
        exclude: set[str] | None = None,
    ) -> int:
        """Recursively upload a local directory tree via per-file SFTP puts.

        Prefer :meth:`upload_directory_tarball` for large trees.
        """
        self._ensure_connected()
        assert self._sftp is not None

        local_root = Path(local_dir)
        if not local_root.is_dir():
            raise FileNotFoundError(f"Local directory not found: {local_dir}")

        entries = self._walk_filtered(local_root, exclude or set())
        created_dirs: set[str] = set()
        count = 0
        for abs_path, arcname in entries:
            remote_file = f"{remote_dir}/{arcname}"
            parent = str(PurePosixPath(remote_file).parent)
            if parent not in created_dirs:
                self.ssh_exec(f"mkdir -p '{parent}'")
                created_dirs.add(parent)
            try:
                self._sftp.put(str(abs_path), remote_file)
                count += 1
            except Exception as exc:
                logger.warning('upload_directory: skip %s: %s', remote_file, exc)

        logger.info('upload_directory: %s -> %s (%d files)', local_dir, remote_dir, count)
        return count

    def upload_directory_tarball(
        self,
        local_dir: str,
        remote_dir: str,
        exclude: set[str] | None = None,
    ) -> int:
        """Upload a local directory as a single tarball (pack → put → extract).

        Much faster than :meth:`upload_directory` for large trees because it
        replaces N×(ssh_exec + sftp.put) with 1×sftp.put + 1×ssh_exec.
        """
        self._ensure_connected()
        assert self._sftp is not None

        local_root = Path(local_dir)
        if not local_root.is_dir():
            raise FileNotFoundError(f"Local directory not found: {local_dir}")

        t0 = time.monotonic()
        entries = self._walk_filtered(local_root, exclude or set())

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            for abs_path, arcname in entries:
                tar.add(str(abs_path), arcname=arcname)
        tar_bytes = buf.getvalue()
        logger.info(
            'upload_directory_tarball: packed %d files (%.1f KB) in %.2fs',
            len(entries),
            len(tar_bytes) / 1024,
            time.monotonic() - t0,
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix='.tar.gz') as tmp:
            tmp.write(tar_bytes)
            tmp_path = tmp.name

        remote_tmp = f"/tmp/_evo_upload_{int(time.monotonic() * 1000)}.tar.gz"
        try:
            t1 = time.monotonic()
            with self._sftp_lock:
                self._sftp.put(tmp_path, remote_tmp)
            logger.info('upload_directory_tarball: sftp.put %.2fs', time.monotonic() - t1)

            t2 = time.monotonic()
            self.ssh_exec(f"mkdir -p '{remote_dir}' && tar -xzf '{remote_tmp}' -C '{remote_dir}'")
            logger.info('upload_directory_tarball: extract %.2fs', time.monotonic() - t2)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            self.ssh_exec(f"rm -f '{remote_tmp}'")

        logger.info(
            'upload_directory_tarball: %s -> %s (%d files) total=%.2fs',
            local_dir,
            remote_dir,
            len(entries),
            time.monotonic() - t0,
        )
        return len(entries)

    def download_file(self, remote_path: str, timeout: int | None = None) -> bytes:
        """Download a remote file into memory via SFTP."""
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            st = self._sftp.stat(remote_path)
            if st.st_mode is not None and stat.S_ISDIR(st.st_mode):
                raise RuntimeError(
                    f"Cannot download directory: {remote_path}. "
                    'Use exec_bash to list directory contents instead.'
                )
            buf = io.BytesIO()
            self._sftp.getfo(remote_path, buf)
            return buf.getvalue()

    def read_file_content(self, remote_path: str, encoding: str = 'utf-8') -> str:
        """Read a remote text file via SFTP."""
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                with self._sftp.open(remote_path, 'r') as f:
                    raw = f.read()
                if isinstance(raw, bytes):
                    return raw.decode(encoding)
                return raw
            except FileNotFoundError:
                raise RuntimeError(f"File not found: {remote_path}")

    def write_file_content(
        self, remote_path: str, content: str, encoding: str = 'utf-8'
    ) -> None:
        """Write content to a remote text file via SFTP."""
        self._ensure_connected()
        assert self._sftp is not None

        remote_dir = str(PurePosixPath(remote_path).parent)
        self.ssh_exec(f"mkdir -p '{remote_dir}'")
        with self._sftp_lock:
            with self._sftp.open(remote_path, 'w') as f:
                f.write(
                    content.encode(encoding) if isinstance(content, str) else content
                )

    def path_exists(self, remote_path: str) -> bool:
        """Check if a remote path exists."""
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                self._sftp.stat(remote_path)
                return True
            except FileNotFoundError:
                return False

    def is_file(self, remote_path: str) -> bool:
        """Check if a remote path is a regular file."""
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                st = self._sftp.stat(remote_path)
                return st.st_mode is not None and stat.S_ISREG(st.st_mode)
            except FileNotFoundError:
                return False

    def is_directory(self, remote_path: str) -> bool:
        """Check if a remote path is a directory."""
        self._ensure_connected()
        assert self._sftp is not None
        with self._sftp_lock:
            try:
                st = self._sftp.stat(remote_path)
                return st.st_mode is not None and stat.S_ISDIR(st.st_mode)
            except FileNotFoundError:
                return False
